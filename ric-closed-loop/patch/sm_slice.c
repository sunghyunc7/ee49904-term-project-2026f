/*
 * EE49904 — drop-in replacement for FlexRIC's
 *   examples/emulator/agent/sm_slice.c
 *
 * UPSTREAM BEHAVIOR: write_ctrl_slice_sm() printed the message type and threw
 * the message away; read_slice_sm() returned fill_slice_ind_data() — random.
 * Control therefore had no effect on anything an xApp could observe.
 *
 * PATCHED BEHAVIOR:
 *   write_ctrl_slice_sm()  parses the requested PRB share of each DL slice and
 *                          applies it to the shared plant.
 *   read_slice_sm()        reports back the shares currently in force, so an
 *                          xApp can confirm that its request was applied —
 *                          desired-state reconciliation, in miniature.
 *
 * SHARE EXTRACTION — three encodings are accepted, matching what FlexRIC's own
 * Python example sends:
 *   STATIC        share = (pos_high - pos_low + 1) / 106 PRB  x 100
 *   NVS CAPACITY  share = pct_reserved
 *   NVS RATE      share = mbps_required / mbps_reference x 100
 * The first DL slice in the list drives plant slice 0, the second plant slice 1.
 * Additional slices are ignored (the plant has two).
 *
 * GUARDRAIL: the plant clamps each share to >= 5% and renormalizes if the pair
 * exceeds 100%. When that happens the request was NOT applied as asked, and the
 * agent says so on stdout. An xApp that never reads back its own configuration
 * will not notice — which is the point of the read-back path.
 *
 * RESET: SLICE_CTRL_SM_V0_DEL is repurposed as an experiment reset. That is a
 * kit invention, not O-RAN semantics — see the DEL branch below for why.
 *
 * LICENSE
 * -------
 * This file is a modified copy of a FlexRIC source file, and is therefore a
 * derivative work of FlexRIC. FlexRIC is published by the OpenAirInterface
 * Software Alliance under the **OAI Public License v1.1**, and this file is
 * distributed under those same terms — read them; the OAI PL is not one of the
 * usual OSI licenses and does not behave like MIT or Apache.
 *   upstream : https://gitlab.eurecom.fr/mosaic5g/flexric  (mirror: https://github.com/openaicellular/flexric)
 *   license  : the LICENSE file at the root of the FlexRIC source tree
 * apply_patch.sh keeps the file it replaces as sm_slice.c.orig, so the original text
 * stays available for comparison.
 *
 * Modifications for KAIST EE49904 (AI-Native Networking), 2026.
 */
#include "sm_slice.h"
#include "ee49904_plant.h"
#include "../../../src/util/time_now_us.h"

#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

void init_slice_sm(void)
{
  ee_plant_init(0.01, 0);
}

void free_slice_sm(void)
{
  // No allocation needed
}

/* ---- helpers -------------------------------------------------------------- */

static double share_from_slice(fr_slice_t const* s)
{
  switch (s->params.type) {
    case SLICE_ALG_SM_V0_STATIC: {
      double prbs = (double)s->params.u.sta.pos_high
                  - (double)s->params.u.sta.pos_low + 1.0;
      if (prbs < 0.0) prbs = 0.0;
      return 100.0 * prbs / (double)EE_TOTAL_PRB;
    }
    case SLICE_ALG_SM_V0_NVS: {
      if (s->params.u.nvs.conf == SLICE_SM_NVS_V0_CAPACITY)
        return (double)s->params.u.nvs.u.capacity.u.pct_reserved;
      /* RATE */
      double req = (double)s->params.u.nvs.u.rate.u1.mbps_required;
      double ref = (double)s->params.u.nvs.u.rate.u2.mbps_reference;
      return (ref > 0.0) ? 100.0 * req / ref : 50.0;
    }
    default:
      return -1.0;                 /* EDF / SCN19: not mapped by this plant */
  }
}

static void fill_one_slice(fr_slice_t* s, uint32_t id, const char* label, double pct)
{
  memset(s, 0, sizeof(*s));
  s->id = id;
  s->len_label = (uint32_t)strlen(label);
  s->label = strdup(label);
  s->len_sched = (uint32_t)strlen("PF");
  s->sched = strdup("PF");
  s->params.type = SLICE_ALG_SM_V0_NVS;
  s->params.u.nvs.conf = SLICE_SM_NVS_V0_CAPACITY;
  s->params.u.nvs.u.capacity.u.pct_reserved = (float)pct;
}

/* ---- indication: report the configuration actually in force ---------------- */

bool read_slice_sm(void* data)
{
  assert(data != NULL);
  slice_ind_data_t* slice = (slice_ind_data_t*)data;
  slice_ind_msg_t* m = &slice->msg;
  memset(m, 0, sizeof(*m));

  double s0 = 50.0, s1 = 50.0;
  ee_plant_get_shares(&s0, &s1);

  m->tstamp = time_now_us();

  m->slice_conf.dl.len_slices = EE_NUM_SLICES;
  m->slice_conf.dl.slices = calloc(EE_NUM_SLICES, sizeof(fr_slice_t));
  assert(m->slice_conf.dl.slices != NULL && "memory exhausted");
  m->slice_conf.dl.len_sched_name = (uint32_t)strlen("NVS");
  m->slice_conf.dl.sched_name = strdup("NVS");
  fill_one_slice(&m->slice_conf.dl.slices[0], 0, "s0", s0);
  fill_one_slice(&m->slice_conf.dl.slices[1], 1, "s1", s1);

  /* uplink is not modeled; report an empty UL configuration */
  m->slice_conf.ul.len_slices = 0;
  m->slice_conf.ul.slices = NULL;
  m->slice_conf.ul.len_sched_name = (uint32_t)strlen("NVS");
  m->slice_conf.ul.sched_name = strdup("NVS");

  /* UE -> slice association: UEs 0,1 -> slice 0; UEs 2,3 -> slice 1 */
  m->ue_slice_conf.len_ue_slice = EE_NUM_UE;
  m->ue_slice_conf.ues = calloc(EE_NUM_UE, sizeof(ue_slice_assoc_t));
  assert(m->ue_slice_conf.ues != NULL && "memory exhausted");
  for (uint32_t i = 0; i < EE_NUM_UE; ++i) {
    m->ue_slice_conf.ues[i].rnti   = (uint16_t)(0x1000 + i);
    m->ue_slice_conf.ues[i].dl_id  = (i < EE_NUM_UE / 2) ? 0u : 1u;
    m->ue_slice_conf.ues[i].ul_id  = (i < EE_NUM_UE / 2) ? 0u : 1u;
  }
  return true;
}

void read_slice_setup_sm(void* data)
{
  assert(data != NULL);
  assert(0 != 0 && "Not supported");
}

/* ---- control: actually change the plant ----------------------------------- */

sm_ag_if_ans_t write_ctrl_slice_sm(void const* data)
{
  assert(data != NULL);

  slice_ctrl_req_data_t const* req = (slice_ctrl_req_data_t const*)data;
  slice_ctrl_msg_t const* msg = &req->msg;

  if (msg->type == SLICE_CTRL_SM_V0_ADD) {
    ul_dl_slice_conf_t const* dl = &msg->u.add_mod_slice.dl;
    double cur0 = 50.0, cur1 = 50.0;
    ee_plant_get_shares(&cur0, &cur1);
    double want0 = cur0, want1 = cur1;

    if (dl->len_slices >= 1) {
      double v = share_from_slice(&dl->slices[0]);
      if (v >= 0.0) want0 = v;
    }
    if (dl->len_slices >= 2) {
      double v = share_from_slice(&dl->slices[1]);
      if (v >= 0.0) want1 = v;
    } else if (dl->len_slices == 1) {
      want1 = 100.0 - want0;             /* one slice given -> the rest is the other */
    }

    bool exact = ee_plant_set_shares(want0, want1);
    double got0, got1;
    ee_plant_get_shares(&got0, &got1);
    /* Print more digits when the guardrail fired — at %.1f a real clamp can
     * look identical to what was asked for, which is exactly the case a
     * student is meant to be able to see. */
    if (exact)
      printf("[E2 Agent] SLICE ADD: requested %.1f/%.1f -> applied %.1f/%.1f\n",
             want0, want1, got0, got1);
    else
      printf("[E2 Agent] SLICE ADD: requested %.3f/%.3f -> applied %.3f/%.3f"
             "  (CLAMPED — request not honored as sent)\n",
             want0, want1, got0, got1);
    fflush(stdout);

  } else if (msg->type == SLICE_CTRL_SM_V0_DEL) {
    /* EE49904 EXTENSION — not O-RAN semantics.
     *
     * Upstream, DEL removes slices from the configuration. This plant has a
     * fixed two-slice structure, so there is nothing to remove. We reuse DEL
     * as the kit's **experiment reset**: it returns the plant to tick 0, empty
     * queues, and a 50/50 split.
     *
     * Why this exists: the plant is a single process-wide object created when
     * the agent starts. Without a reset, a second experiment inherits the
     * backlog and the demand phase left behind by the first one, and the two
     * runs are not comparable. Restarting emu_agent_gnb also works and is the
     * honest thing to do on real hardware; this is the cheap version.
     *
     * Students should know it is a kit invention. See BRIEF.md Sec. 2. */
    ee_plant_reset();
    printf("[E2 Agent] SLICE DEL: **plant reset** — tick 0, queues empty, shares 50.0/50.0\n"
           "           (EE49904 kit extension, not standard O-RAN DEL semantics)\n");
    fflush(stdout);

  } else if (msg->type == SLICE_CTRL_SM_V0_UE_SLICE_ASSOC) {
    /* The plant's UE->slice mapping is fixed; association requests are
     * acknowledged but do not move UEs. Say so rather than pretending. */
    printf("[E2 Agent] SLICE ASSOC rx — ignored (plant has a fixed UE mapping)\n");
    fflush(stdout);

  } else {
    printf("[E2 Agent] SLICE control: unknown msg type %d — ignored\n", (int)msg->type);
    fflush(stdout);
  }

  sm_ag_if_ans_t ans = {.type = CTRL_OUTCOME_SM_AG_IF_ANS_V0};
  ans.ctrl_out.type = SLICE_AGENT_IF_CTRL_ANS_V0;
  return ans;
}
