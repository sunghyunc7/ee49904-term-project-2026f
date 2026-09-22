/*
 * EE49904 AI-Native Networking — stateful plant for the FlexRIC emulated E2 node.
 *
 * WHY THIS EXISTS
 * ---------------
 * FlexRIC's emulated E2 agent is a *protocol* emulator: every service model's
 * read_*_sm() returns randomly generated data (test/rnd/fill_rnd_data_*), and
 * write_ctrl_*_sm() parses the control message, prints it, and discards it.
 * The E2AP path is real; the RAN behind it has no state. A control loop built on
 * the stock emulator is therefore not closed — the KPIs never respond to control.
 *
 * This file adds the missing state: a deliberately simple downlink scheduler
 * shared between two slices. SLICE control sets each slice's PRB share; MAC
 * indications report what that allocation actually produced. Now the loop closes.
 *
 * WHAT THIS IS NOT
 * ----------------
 * It is a caricature, not a RAN. No HARQ, no fading correlation, no real
 * scheduler, no uplink to speak of. It exists so that "the KPI moved because I
 * changed the control" is a true sentence. Saying precisely what it does not
 * model is part of the assignment.
 *
 * Deterministic: same seed -> same trajectory. Thread-safe: the E2 agent reads
 * from its indication thread while control arrives on another.
 *
 * LICENSE
 * -------
 * Written from scratch for KAIST EE49904 (AI-Native Networking), 2026. It contains
 * no FlexRIC code and is not a derivative of it.
 * Note what happens at build time, though: this file is compiled into
 * emu_agent_gnb, and *that binary* is a FlexRIC derivative — the OAI Public
 * License v1.1 governs the binary you produce and anything you distribute of it.
 */
#ifndef EE49904_PLANT_H
#define EE49904_PLANT_H

#include <stdint.h>
#include <stdbool.h>

#define EE_NUM_SLICES 2
#define EE_NUM_UE     4          /* UEs 0,1 -> slice 0;  UEs 2,3 -> slice 1 */
#define EE_TOTAL_PRB  106        /* 20 MHz, 30 kHz SCS */

typedef struct {
  uint32_t rnti;
  uint32_t slice;                /* which slice this UE belongs to          */
  double   offered_bps;          /* current offered load (bits/s)           */
  double   backlog_bits;         /* queued but unserved                     */
  double   served_bps;           /* throughput achieved in the last tick    */
  uint64_t aggr_bytes;           /* cumulative served bytes                 */
  uint32_t prb_used;             /* PRBs consumed in the last tick          */
  uint8_t  cqi;                  /* 1..15, varies slowly                    */
  uint8_t  mcs;                  /* derived from cqi                        */
  double   snr_db;
} ee_ue_t;

typedef struct {
  double   share_pct[EE_NUM_SLICES];  /* PRB share per slice; sums to <=100 */
  ee_ue_t  ue[EE_NUM_UE];
  uint64_t tick;                      /* indication counter                 */
  double   dt_s;                      /* seconds per tick                   */
  uint64_t seed;
  bool     initialized;
} ee_plant_t;

/* Initialize (idempotent). dt_s should match the subscription period. */
void ee_plant_init(double dt_s, uint64_t seed);

/* Advance one tick and copy out the current state. Called from read_mac_sm(). */
void ee_plant_step(ee_plant_t* out);

/* Set slice PRB shares (percent). Called from write_ctrl_slice_sm().
 * Values are clamped to [EE_MIN_SHARE, 100] and renormalized if they exceed 100.
 * Returns true if the request was applied as given, false if it was clamped. */
bool ee_plant_set_shares(double s0_pct, double s1_pct);

/* Current shares, for the SLICE indication read-back. */
void ee_plant_get_shares(double* s0_pct, double* s1_pct);

/* Full reset — test/demo only, so scenarios start from identical state. */
void ee_plant_reset(void);

/* Snapshot without advancing time (SLICE indication uses this). */
void ee_plant_peek(ee_plant_t* out);

#define EE_MIN_SHARE 5.0

#endif /* EE49904_PLANT_H */
