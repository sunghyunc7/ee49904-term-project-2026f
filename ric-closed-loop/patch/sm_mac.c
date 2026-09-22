/*
 * EE49904 — drop-in replacement for FlexRIC's
 *   examples/emulator/agent/sm_mac.c
 *
 * UPSTREAM BEHAVIOR: read_mac_sm() called fill_mac_ind_data(), which returns a
 * random number of UEs with random counters (test/rnd/fill_rnd_data_mac.c).
 *
 * PATCHED BEHAVIOR: MAC indications now report the state of the shared plant
 * (ee49904_plant.c). Per-UE throughput, PRB usage, CQI and buffer occupancy are
 * consequences of the PRB shares that SLICE control set. That is what makes the
 * loop close.
 *
 * Field mapping — read this before interpreting anything in an xApp:
 *   rnti               UE identity (0x1000 + index), stable across indications
 *   dl_aggr_tbs        cumulative served bytes  (monotonic; differentiate it)
 *   dl_curr_tbs        bytes served in the last tick
 *   bsr                buffer status = current backlog in BYTES  <- the control signal
 *   dl_aggr_prb        PRBs used in the last tick
 *   wb_cqi / dl_mcs1   radio quality
 *   pusch_snr          SNR in dB
 *   dl_bler            0 here: this plant does not model errors. Do not use it.
 *
 * Slice membership is not carried in the MAC indication. UEs 0,1 (rnti 0x1000,
 * 0x1001) belong to slice 0 and UEs 2,3 (0x1002, 0x1003) to slice 1. An xApp
 * that reads only MAC indications has to learn that mapping from somewhere
 * else, which is itself worth noticing.
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
 * apply_patch.sh keeps the file it replaces as sm_mac.c.orig, so the original text
 * stays available for comparison.
 *
 * Modifications for KAIST EE49904 (AI-Native Networking), 2026.
 */
#include "sm_mac.h"
#include "ee49904_plant.h"
#include "../../../src/util/time_now_us.h"

#include <assert.h>
#include <stdlib.h>
#include <string.h>

void init_mac_sm(void)
{
  ee_plant_init(0.01, 0);          /* 10 ms; harmless if already initialized */
}

void free_mac_sm(void)
{
  // No allocation needed
}

bool read_mac_sm(void* data)
{
  assert(data != NULL);

  mac_ind_data_t* mac = (mac_ind_data_t*)data;
  mac_ind_msg_t* m = &mac->msg;

  ee_plant_t p;
  ee_plant_step(&p);               /* advance the plant one indication period */

  m->len_ue_stats = EE_NUM_UE;
  m->tstamp = time_now_us();
  m->ue_stats = calloc(EE_NUM_UE, sizeof(mac_ue_stats_impl_t));
  assert(m->ue_stats != NULL && "memory exhausted");

  for (uint32_t i = 0; i < EE_NUM_UE; ++i) {
    const ee_ue_t* u = &p.ue[i];
    mac_ue_stats_impl_t* s = &m->ue_stats[i];
    memset(s, 0, sizeof(*s));

    s->rnti        = u->rnti;
    s->dl_aggr_tbs = u->aggr_bytes;
    s->dl_curr_tbs = (uint64_t)(u->served_bps * p.dt_s / 8.0);
    s->dl_aggr_bytes_sdus = u->aggr_bytes;
    s->bsr         = (uint32_t)(u->backlog_bits / 8.0);   /* backlog, bytes */
    s->dl_aggr_prb = u->prb_used;
    s->dl_sched_rb = u->prb_used;
    s->wb_cqi      = u->cqi;
    s->dl_mcs1     = u->mcs;
    s->pusch_snr   = (float)u->snr_db;
    s->pucch_snr   = (float)u->snr_db;
    s->dl_bler     = 0.0f;          /* not modeled — see header comment */
    s->ul_bler     = 0.0f;
    s->frame       = (uint16_t)((p.tick / 10) % 1024);
    s->slot        = (uint16_t)(p.tick % 10);
  }
  return true;
}

void read_mac_setup_sm(void* data)
{
  assert(data != NULL);
  assert(0 != 0 && "Not supported");
}

sm_ag_if_ans_t write_ctrl_mac_sm(void const* data)
{
  /* Unchanged from upstream. The Python xApp SDK does not bind control_mac_sm,
   * so MAC control is not part of this assignment; SLICE control is. */
  assert(data != NULL);

  mac_ctrl_req_data_t* ctrl = (mac_ctrl_req_data_t*)data;
  assert(ctrl->hdr.dummy == 1);
  assert(ctrl->msg.action == 42);

  sm_ag_if_ans_t ans = {.type = CTRL_OUTCOME_SM_AG_IF_ANS_V0 };
  return ans;
}
