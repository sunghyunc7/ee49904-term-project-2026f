/*
 * EE49904 — stateful plant for the FlexRIC emulated E2 node.  See header.
 *
 * LICENSE
 * -------
 * Written from scratch for KAIST EE49904 (AI-Native Networking), 2026. It contains
 * no FlexRIC code and is not a derivative of it.
 * Note what happens at build time, though: this file is compiled into
 * emu_agent_gnb, and *that binary* is a FlexRIC derivative — the OAI Public
 * License v1.1 governs the binary you produce and anything you distribute of it.
 */
#include "ee49904_plant.h"

#include <math.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static ee_plant_t g;
static pthread_mutex_t g_mtx = PTHREAD_MUTEX_INITIALIZER;

/* ---- deterministic PRNG (no rand(), so the trajectory is reproducible) ---- */
static uint64_t rng_s;
static double rng_u(void)          /* uniform [0,1) */
{
  rng_s ^= rng_s << 13; rng_s ^= rng_s >> 7; rng_s ^= rng_s << 17;
  return (double)(rng_s >> 11) / 9007199254740992.0;
}

/* Spectral efficiency (bit/s/Hz) per CQI, 3GPP-flavored but rounded. */
static double cqi_eff(uint8_t cqi)
{
  static const double e[16] = {0.0,
    0.15, 0.23, 0.38, 0.60, 0.88, 1.18, 1.48, 1.91,
    2.41, 2.73, 3.32, 3.90, 4.52, 5.12, 5.55};
  if (cqi > 15) cqi = 15;
  return e[cqi];
}

/* One PRB = 12 subcarriers x 30 kHz = 360 kHz. Bits per PRB per second. */
static double prb_bps(uint8_t cqi) { return 360000.0 * cqi_eff(cqi); }

/* Nominal cell capacity at a typical CQI — the yardstick the load is expressed in. */
#define EE_NOMINAL_CQI  10
#define EE_LOAD_PERIOD  30.0     /* seconds for one full demand swing */
#define EE_LOAD_MEAN    0.45     /* each slice averages 45% of cell capacity  */
#define EE_LOAD_SWING   0.30     /* ...swinging +/-30 points, in ANTI-PHASE   */

static double nominal_cell_bps(void)
{
  return (double)EE_TOTAL_PRB * 360000.0 * 2.41;   /* CQI 10 -> 2.41 bit/s/Hz */
}

/* Offered load per UE.
 *
 * The two slices swing in ANTI-PHASE: when slice 0 wants 75% of the cell,
 * slice 1 wants 15%, and vice versa. Total demand stays near 90% of capacity,
 * so the cell can always serve everyone — but only if the PRB split follows
 * the demand. A fixed 50/50 split starves whichever slice is currently heavy
 * while the other wastes PRBs it cannot use. That asymmetry is the control
 * problem this plant exists to pose. */
static double offered_for(uint32_t i, uint64_t tick, double dt_s)
{
  double t = (double)tick * dt_s;
  double w = sin(2.0 * M_PI * t / EE_LOAD_PERIOD);
  uint32_t slice = (i < EE_NUM_UE / 2) ? 0u : 1u;
  double frac = EE_LOAD_MEAN + (slice == 0 ? +1.0 : -1.0) * EE_LOAD_SWING * w;
  if (frac < 0.05) frac = 0.05;
  double ues_in_slice = (double)(EE_NUM_UE / 2);
  return nominal_cell_bps() * frac / ues_in_slice;     /* split within the slice */
}

void ee_plant_init(double dt_s, uint64_t seed)
{
  pthread_mutex_lock(&g_mtx);
  if (!g.initialized) {
    memset(&g, 0, sizeof(g));
    g.dt_s = (dt_s > 0.0) ? dt_s : 0.01;
    /* The plant advances dt_s per MAC indication, so dt_s has to equal the
     * subscription period or plant time drifts away from wall time (subscribe at
     * 1 ms with dt_s = 10 ms and the 30 s demand cycle passes in 3 s). The agent
     * cannot see the period an xApp will ask for, so it is given here, once, at
     * agent start-up:  EE49904_PLANT_DT_MS=5 emu_agent_gnb
     * starter/run_experiment.sh sets it from --ind-period. Default: 10 ms. */
    const char* e = getenv("EE49904_PLANT_DT_MS");
    if (e != NULL) {
      double ms = atof(e);
      if (ms >= 0.1 && ms <= 1000.0) g.dt_s = ms / 1000.0;
      else fprintf(stderr, "[ee49904] ignoring EE49904_PLANT_DT_MS='%s' (want 0.1..1000)\n", e);
      static bool said = false;       /* reset re-enters here; say it once */
      if (!said) fprintf(stderr, "[ee49904] plant dt = %.1f ms per indication\n", g.dt_s * 1000.0);
      said = true;
    }
    g.seed = seed ? seed : 0x2026FEEDULL;
    rng_s  = g.seed;
    g.share_pct[0] = 50.0;
    g.share_pct[1] = 50.0;
    for (uint32_t i = 0; i < EE_NUM_UE; ++i) {
      g.ue[i].rnti   = 0x1000 + i;
      g.ue[i].slice  = (i < EE_NUM_UE / 2) ? 0u : 1u;
      g.ue[i].snr_db = 12.0 + 6.0 * rng_u();
      g.ue[i].cqi    = 9;
      g.ue[i].mcs    = 16;
    }
    g.initialized = true;
  }
  pthread_mutex_unlock(&g_mtx);
}

/* The guardrail must not cry wolf.
 *
 * An xApp that asks for 68.9616 / 31.0384 has asked for exactly 100%, but the
 * two numbers travel over E2 as 32-bit floats and arrive summing to
 * 100.0000000000001. A bare `sum > 100.0` test then reports every single
 * control request as clamped, and the one signal a student is supposed to
 * trust — "your request was not honored as sent" — becomes noise.
 *
 * EE_SHARE_EPS is the tolerance below which a deviation is arithmetic, not
 * policy. 1e-3 share points is 0.001 PRB out of 106: far below anything a
 * controller can act on, and comfortably above float32 round-trip error
 * (~7.6e-6 at a share of 69). Renormalization still happens whenever the pair
 * exceeds 100 — the invariant is kept — but it is only *reported* when the
 * correction is large enough to matter. */
#define EE_SHARE_EPS 1e-3

bool ee_plant_set_shares(double s0, double s1)
{
  bool exact = true;
  pthread_mutex_lock(&g_mtx);
  if (s0 < EE_MIN_SHARE) { if (s0 < EE_MIN_SHARE - EE_SHARE_EPS) exact = false; s0 = EE_MIN_SHARE; }
  if (s1 < EE_MIN_SHARE) { if (s1 < EE_MIN_SHARE - EE_SHARE_EPS) exact = false; s1 = EE_MIN_SHARE; }
  if (s0 > 100.0) { if (s0 > 100.0 + EE_SHARE_EPS) exact = false; s0 = 100.0; }
  if (s1 > 100.0) { if (s1 > 100.0 + EE_SHARE_EPS) exact = false; s1 = 100.0; }
  double sum = s0 + s1;
  if (sum > 100.0) {                       /* renormalize, keep the ratio */
    if (sum > 100.0 + EE_SHARE_EPS) exact = false;
    s0 = s0 * 100.0 / sum;
    s1 = s1 * 100.0 / sum;
  }
  g.share_pct[0] = s0;
  g.share_pct[1] = s1;
  pthread_mutex_unlock(&g_mtx);
  return exact;
}

/* Full reset for comparing scenarios. For tests and demos only; the agent does not use it. */
void ee_plant_reset(void)
{
  pthread_mutex_lock(&g_mtx);
  double dt = g.dt_s ? g.dt_s : 0.01;
  uint64_t seed = g.seed ? g.seed : 0x2026FEEDULL;
  memset(&g, 0, sizeof(g));
  pthread_mutex_unlock(&g_mtx);
  ee_plant_init(dt, seed);
}

void ee_plant_get_shares(double* s0, double* s1)
{
  pthread_mutex_lock(&g_mtx);
  if (s0) *s0 = g.share_pct[0];
  if (s1) *s1 = g.share_pct[1];
  pthread_mutex_unlock(&g_mtx);
}

void ee_plant_peek(ee_plant_t* out)
{
  pthread_mutex_lock(&g_mtx);
  if (out) *out = g;
  pthread_mutex_unlock(&g_mtx);
}

void ee_plant_step(ee_plant_t* out)
{
  pthread_mutex_lock(&g_mtx);
  if (!g.initialized) {
    pthread_mutex_unlock(&g_mtx);
    ee_plant_init(0.01, 0);
    pthread_mutex_lock(&g_mtx);
  }
  const double dt = g.dt_s;

  /* PRBs available to each slice this tick */
  double prb_slice[EE_NUM_SLICES];
  for (uint32_t s = 0; s < EE_NUM_SLICES; ++s)
    prb_slice[s] = EE_TOTAL_PRB * g.share_pct[s] / 100.0;

  /* count UEs per slice so the share is split evenly inside a slice */
  uint32_t n_in[EE_NUM_SLICES] = {0};
  for (uint32_t i = 0; i < EE_NUM_UE; ++i) n_in[g.ue[i].slice]++;

  for (uint32_t i = 0; i < EE_NUM_UE; ++i) {
    ee_ue_t* u = &g.ue[i];

    /* Radio conditions: mean-reverting (Ornstein-Uhlenbeck-ish) around a
     * per-UE mean, NOT a free random walk. A free walk parks a UE at the SNR
     * floor for long stretches and the experiment stops being about
     * allocation and starts being about luck. */
    const double snr_mean = 16.0 + 2.0 * (double)(i % 2);
    u->snr_db += 0.02 * (snr_mean - u->snr_db) + 0.15 * (rng_u() - 0.5);
    if (u->snr_db < 6.0)  u->snr_db = 6.0;
    if (u->snr_db > 24.0) u->snr_db = 24.0;
    int cqi = (int)(u->snr_db * 0.6) + 1;
    if (cqi < 1)  cqi = 1;
    if (cqi > 15) cqi = 15;
    u->cqi = (uint8_t)cqi;
    u->mcs = (uint8_t)((cqi * 28) / 15);

    /* demand */
    u->offered_bps = offered_for(i, g.tick, dt);
    u->backlog_bits += u->offered_bps * dt;

    /* capacity from this UE's slice share */
    double my_prb  = prb_slice[u->slice] / (double)(n_in[u->slice] ? n_in[u->slice] : 1);
    double cap_bits = my_prb * prb_bps(u->cqi) * dt;

    double served = (u->backlog_bits < cap_bits) ? u->backlog_bits : cap_bits;
    u->backlog_bits -= served;
    if (u->backlog_bits > 5e8) u->backlog_bits = 5e8;   /* don't overflow forever */
    u->served_bps = served / dt;
    u->aggr_bytes += (uint64_t)(served / 8.0);
    u->prb_used = (uint32_t)((cap_bits > 0.0) ? (my_prb * served / cap_bits) : 0.0);
  }

  g.tick++;
  if (out) *out = g;
  pthread_mutex_unlock(&g_mtx);
}
