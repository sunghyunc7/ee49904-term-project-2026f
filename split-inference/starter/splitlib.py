"""
EE49904 Term Project — split inference shared library.

Defines how a model is cut into two pieces, a "head (device)" and a "tail (edge/cloud)",
and the size of the intermediate tensor that crosses at each split point.
Both split_bench.py and split_serve.py use this file.

To add a new model, just add one entry to MODELS.
"""
import torch
import torch.nn as nn


# --------------------------------------------------------------------------
# Model registry — each model is expressed as a sequence of (stage name, module).
# The device runs up to stage i, and that output tensor is sent over the network.
# --------------------------------------------------------------------------
def _resnet_stages(m):
    return [
        ("stem", nn.Sequential(m.conv1, m.bn1, m.relu, m.maxpool)),
        ("layer1", m.layer1),
        ("layer2", m.layer2),
        ("layer3", m.layer3),
        ("layer4", m.layer4),
        ("head", nn.Sequential(m.avgpool, nn.Flatten(1), m.fc)),
    ]


def _mobilenet_stages(m):
    # split features into 5 roughly equal chunks (boundaries fall near where the stride changes)
    cuts = [2, 4, 7, 13, 17]
    stages, prev = [], 0
    for i, c in enumerate(cuts):
        stages.append((f"features{prev}_{c}", nn.Sequential(*list(m.features[prev:c]))))
        prev = c
    stages.append(("head", nn.Sequential(m.avgpool, nn.Flatten(1), m.classifier)))
    return stages


def _build(name, pretrained):
    import torchvision.models as tv
    w = "DEFAULT" if pretrained else None
    if name == "resnet18":
        return _resnet_stages(tv.resnet18(weights=w))
    if name == "resnet50":
        return _resnet_stages(tv.resnet50(weights=w))
    if name == "mobilenet_v3_large":
        return _mobilenet_stages(tv.mobilenet_v3_large(weights=w))
    raise ValueError(f"unknown model: {name}. available: {', '.join(MODELS)}")


MODELS = ["resnet18", "resnet50", "mobilenet_v3_large"]
INPUT_SHAPE = (1, 3, 224, 224)

# Which precision the intermediate tensor is sent in → the byte count changes.
DTYPE_BYTES = {"fp32": 4, "fp16": 2, "int8": 1}


def build_model(name, pretrained=False, channels_last=False):
    """
    Returns (stage_names, module_list). eval mode, grad disabled.

    channels_last=True uses the NHWC memory layout. On CPU, the depthwise convolution family
    (MobileNet etc.) gets several times faster from this one line — so "device compute time" is
    not a property of the model alone but of (model × runtime × memory layout).
    """
    stages = _build(name, pretrained)
    mods = nn.ModuleList([m for _, m in stages]).eval()
    for p in mods.parameters():
        p.requires_grad_(False)
    if channels_last:
        mods = mods.to(memory_format=torch.channels_last)
    return [n for n, _ in stages], mods


def split_points(stage_names):
    """
    List of split points. From 'input' (do nothing and send the raw input as is) up to just
    before the last stage. After the last stage means 'run everything on the device', so it is
    listed separately as 'all_local'.
    """
    return ["input"] + list(stage_names[:-1]) + ["all_local"]


@torch.no_grad()
def profile_stages(mods, input_shape=INPUT_SHAPE, repeats=20, warmup=5, seed=0,
                   channels_last=False):
    """
    Measures the run time (s) of each stage and the shape/element count of its 'output' tensor.

    The estimator is the **minimum**. Why the minimum and not the median: contention with other
    processes only contaminates a measurement upward (never downward). So the minimum is closest
    to "the time this machine actually needs for this operation". Measured on a laptop with a
    browser open, the median swings by up to 2x, and then the optimal split point changes —
    this instability is itself material for Question 2.

    Returns: (stage_s, out_shape, out_numel, input_numel, spread)
      spread[i] = (min, median, max) in seconds — to check how much the measurement jittered
    """
    import time
    torch.manual_seed(seed)
    x = torch.randn(*input_shape)
    if channels_last:
        x = x.to(memory_format=torch.channels_last)
    times, shapes, numels, spread = [], [], [], []
    h = x
    for mod in mods:
        for _ in range(warmup):
            out = mod(h)
        samples = []
        for _ in range(repeats):
            t0 = time.perf_counter()
            out = mod(h)
            samples.append(time.perf_counter() - t0)
        samples.sort()
        times.append(samples[0])                       # minimum
        spread.append((samples[0], samples[len(samples) // 2], samples[-1]))
        h = out
        shapes.append(tuple(h.shape))
        numels.append(h.numel())
    return times, shapes, numels, x.numel(), spread


def payload_bytes(numel, dtype):
    return numel * DTYPE_BYTES[dtype]


def link_time_s(nbytes, bandwidth_mbps, rtt_ms):
    """Simple link model: transfer time = bytes/bandwidth, plus one RTT."""
    return (nbytes * 8.0) / (bandwidth_mbps * 1e6) + (rtt_ms / 1000.0)


def total_latency_s(split, stage_ms, numels, input_numel, dtype, bw_mbps, rtt_ms,
                    tail_speedup=20.0, device_slowdown=1.0):
    """
    End-to-end latency (s) for a split name.
      - 'input'     : device compute 0, send the raw input, everything runs remotely
      - 'all_local' : transfer 0, everything runs on the device
      - otherwise   : device up to that stage, send its output, the rest remote

    Stage times are measured on **the machine that ran this benchmark**. Two factors rescale them:
      device_slowdown : how many times slower the real device is than this machine (5~20 for a phone)
      tail_speedup    : how many times faster the remote server is than this machine (10~50 for a server GPU)
    If both are 1.0 it means "both sides are the same as this machine", and then offloading almost
    always loses — that is expected, and choosing the scenario is part of the project.
    """
    n = len(stage_ms)
    ds, ts = max(device_slowdown, 1e-9), max(tail_speedup, 1e-9)
    if split == "input":
        dev, remote_idx, nb = 0.0, range(0, n), payload_bytes(input_numel, dtype)
    elif split == "all_local":
        return sum(stage_ms) * ds
    else:
        k = SPLIT_INDEX[split]
        dev = sum(stage_ms[: k + 1]) * ds
        remote_idx = range(k + 1, n)
        nb = payload_bytes(numels[k], dtype)
    remote = sum(stage_ms[i] for i in remote_idx) / ts
    return dev + link_time_s(nb, bw_mbps, rtt_ms) + remote


SPLIT_INDEX = {}   # filled per model by split_bench.py


def set_split_index(stage_names):
    SPLIT_INDEX.clear()
    for i, n in enumerate(stage_names):
        SPLIT_INDEX[n] = i
