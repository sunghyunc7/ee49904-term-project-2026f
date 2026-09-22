/*
 * EE49904 — standalone unit test for the plant.
 *
 * Even without SCTP, **the plant itself** can be verified. The E2 wiring (subscription, indication, control)
 * must be checked separately, but "does control really change the KPIs?" is settled here.
 *
 *   cc -O2 -o test_plant test_plant.c ee49904_plant.c -lm -lpthread && ./test_plant
 *
 * Runs four scenarios:
 *   A. open loop 50/50                   — backlog must build up when the load shifts
 *   B. closed loop, low gain, delay 0     — backlog must be suppressed
 *   C. closed loop, low gain, delay 500ms — must stay stable even with delay
 *   D. closed loop, high gain, delay 500ms — must oscillate
 *
 * B, C and D run the same controller on the same plant. C and D differ from B only in the age of
 * the observation the controller acts on; D differs from C only in the gain. What that says about
 * the instability you will meet in the real E2 loop is yours to work out (BRIEF Q2.3) — the
 * arguments below let you move one knob at a time.
 *
 * Your own operating point (Q2.2, Q2.3) — give arguments and the four scenarios are skipped:
 *
 *   ./test_plant <gain> [delay_ms] [period_ms] [trace.csv]
 *   ./test_plant 2.0 0 100            # the shipped xApp's gain and control period, no loop delay
 *   ./test_plant 20 150 100 off.csv   # same, with 150 ms of loop delay, trace written to off.csv
 *
 *   gain       same unit as the xApp's --gain (share points per second per Mbit of imbalance)
 *   delay_ms   age of the observation the controller acts on (default 0)
 *   period_ms  control period, like the xApp's --period (default = one tick)
 *   Both are rounded to whole ticks. The tick is 10 ms unless EE49904_PLANT_DT_MS says otherwise.
 *   The trace has the same columns as the xApp's CSV, one row per 100 ms of plant time.
 *
 * LICENSE
 * -------
 * Written from scratch for KAIST EE49904 (AI-Native Networking), 2026. It contains
 * no FlexRIC code, is not a derivative of it, and links against nothing from it —
 * this test builds and runs on its own.
 */
#include "ee49904_plant.h"
#include <stdio.h>
#include <math.h>
#include <string.h>
#include <stdlib.h>

static double DT    = 0.01;   /* s per tick = indication period; main() reads it back from the plant */
static int    TICKS = 6000;   /* 60 s */

typedef struct { double peak_backlog_mb, mean_backlog_mb, mean_tput_mbps, share_std; } res_t;

static double slice_backlog_bits(const ee_plant_t* p, uint32_t s)
{
  double b = 0.0;
  for (int i = 0; i < EE_NUM_UE; ++i) if (p->ue[i].slice == s) b += p->ue[i].backlog_bits;
  return b;
}
static double slice_offered_bps(const ee_plant_t* p, uint32_t s)
{
  double b = 0.0;
  for (int i = 0; i < EE_NUM_UE; ++i) if (p->ue[i].slice == s) b += p->ue[i].offered_bps;
  return b;
}
static double total_served_bps(const ee_plant_t* p)
{
  double b = 0.0;
  for (int i = 0; i < EE_NUM_UE; ++i) b += p->ue[i].served_bps;
  return b;
}

/* gain <= 0  ->  open loop (never touches the shares).
 * gain is in "share points per second / Mbit of backlog imbalance". It is scaled by dt. */
static res_t run_p(double gain, int delay_ticks, int period_ticks, const char* label,
                   int verbose, FILE* csv)
{
  ee_plant_reset();                 /* every scenario starts from the same initial state */
  ee_plant_set_shares(50.0, 50.0);
  ee_plant_t p;

  double peak = 0.0, sum_b = 0.0, sum_t = 0.0;
  double sh_sum = 0.0, sh_sq = 0.0;
  static double err_hist[4096];
  memset(err_hist, 0, sizeof(err_hist));
  if (delay_ticks < 0) delay_ticks = 0;
  if (delay_ticks > 4000) delay_ticks = 4000;
  if (period_ticks < 1) period_ticks = 1;
  int row_every = (int)(0.1 / DT + 0.5); if (row_every < 1) row_every = 1;
  if (csv) fprintf(csv, "t_s,share0,backlog0_bytes,backlog1_bytes,tput0_bytes,tput1_bytes\n");
  for (int t = 0; t < TICKS; ++t) {
    ee_plant_step(&p);
    double b0 = slice_backlog_bits(&p, 0), b1 = slice_backlog_bits(&p, 1);
    double tot = b0 + b1;
    if (tot > peak) peak = tot;
    sum_b += tot;
    sum_t += total_served_bps(&p);

    double s0, s1; ee_plant_get_shares(&s0, &s1);
    sh_sum += s0; sh_sq += s0 * s0;

    err_hist[t % 4096] = (b0 - b1) / 1e6;           /* error in Mbit */
    if (gain > 0.0 && t % period_ticks == 0) {
      /* Integral control. The xApp decides on an indication that is delay_ticks old,
       * once per control period, and scales by that period — as decide() does. */
      int src = t - delay_ticks;
      double err = (src >= 0) ? err_hist[src % 4096] : 0.0;
      double ns0 = s0 + gain * err * DT * period_ticks;   /* gain per second -> per decision */
      if (ns0 < EE_MIN_SHARE) ns0 = EE_MIN_SHARE;
      if (ns0 > 100.0 - EE_MIN_SHARE) ns0 = 100.0 - EE_MIN_SHARE;
      ee_plant_set_shares(ns0, 100.0 - ns0);
    }
    if (csv && t % row_every == 0) {
      double c0, c1; ee_plant_get_shares(&c0, &c1);
      double tp[2] = {0.0, 0.0};
      for (int i = 0; i < EE_NUM_UE; ++i) tp[p.ue[i].slice] += p.ue[i].served_bps * DT / 8.0;
      fprintf(csv, "%.3f,%.2f,%.0f,%.0f,%.0f,%.0f\n", t * DT, c0, b0 / 8.0, b1 / 8.0, tp[0], tp[1]);
    }
    if (verbose && t % (TICKS / 6) == 0) {
      double s0v, s1v; ee_plant_get_shares(&s0v, &s1v);
      printf("      t=%5.1fs  share=%5.1f/%5.1f  backlog=%7.2f/%7.2f Mb  "
             "offered=%5.1f/%5.1f Mb/s\n",
             t * DT, s0v, s1v, b0 / 1e6, b1 / 1e6,
             slice_offered_bps(&p, 0) / 1e6, slice_offered_bps(&p, 1) / 1e6);
    }
  }
  double n = (double)TICKS;
  res_t r = { peak / 1e6, sum_b / n / 1e6, sum_t / n / 1e6,
              sqrt(sh_sq / n - (sh_sum / n) * (sh_sum / n)) };
  printf("  %-22s peak backlog %8.2f Mb | mean %7.2f Mb | tput %6.2f Mb/s | share σ %5.2f\n",
         label, r.peak_backlog_mb, r.mean_backlog_mb, r.mean_tput_mbps, r.share_std);
  return r;
}

static res_t run(double gain, int delay_ticks, const char* label, int verbose)
{
  return run_p(gain, delay_ticks, 1, label, verbose, NULL);
}

/* ./test_plant <gain> [delay_ms] [period_ms] [trace.csv] — one run at the caller's operating point. */
static int custom(int argc, char** argv)
{
  double gain = atof(argv[1]);
  double delay_ms  = (argc > 2) ? atof(argv[2]) : 0.0;
  double period_ms = (argc > 3) ? atof(argv[3]) : DT * 1000.0;
  int dticks = (int)(delay_ms  / (DT * 1000.0) + 0.5);
  int pticks = (int)(period_ms / (DT * 1000.0) + 0.5);
  if (pticks < 1) pticks = 1;
  if (dticks > 4000) { fprintf(stderr, "delay too long (max %.0f ms)\n", 4000 * DT * 1000.0); return 2; }
  FILE* csv = NULL;
  if (argc > 4 && !(csv = fopen(argv[4], "w"))) { perror(argv[4]); return 2; }

  printf(" offline plant: tick %.1f ms | gain %.3g /s/Mbit | delay %.0f ms (%d ticks) | "
         "control period %.0f ms (%d ticks)\n\n",
         DT * 1000.0, gain, dticks * DT * 1000.0, dticks, pticks * DT * 1000.0, pticks);
  run(-1.0, 0, "open-loop 50/50", 0);
  run_p(gain, dticks, pticks, (gain > 0.0) ? "your setting" : "open loop (gain<=0)", 1, csv);
  if (csv) { fclose(csv); printf("  trace -> %s\n", argv[4]); }
  return 0;
}

int main(int argc, char** argv)
{
  ee_plant_init(DT, 0x2026FEEDULL);
  { ee_plant_t q; ee_plant_peek(&q); DT = q.dt_s; TICKS = (int)(60.0 / DT + 0.5); }
  if (argc > 1) {
    if (argv[1][0] == '-' && (argv[1][1] == 'h' || argv[1][1] == '-')) {
      printf("usage: %s                  the four built-in scenarios + pass/FAIL\n"
             "       %s <gain> [delay_ms] [period_ms] [trace.csv]\n", argv[0], argv[0]);
      return 0;
    }
    return custom(argc, argv);
  }

  printf("==============================================================\n");
  printf(" EE49904 plant unit test  (dt=%.0f ms, %d s per scenario)\n", DT * 1000, (int)(TICKS * DT));
  printf("==============================================================\n\n");

  printf("  --- A. open loop 50/50 (no control) ---\n");
  res_t open_ = run(-1.0, 0, "A open-loop 50/50", 1);

  printf("\n  --- B. closed loop, low gain, delay 0 ---\n");
  res_t closed = run(2.0, 0, "B g=2/s delay=0", 1);

  printf("\n  --- C. closed loop, low gain, delay 500 ms ---\n");
  int d500 = (int)(0.5 / DT + 0.5);
  res_t del = run(2.0, d500, "C g=2/s delay=500ms", 0);

  printf("\n  --- D. closed loop, high gain, delay 500 ms ---\n");
  res_t hi = run(60.0, d500, "D g=60/s delay=500ms", 0);

  printf("\n--------------------------------------------------------------\n");
  int fail = 0;

  /* 1) Does control change the KPIs? — this is the reason the patch exists */
  if (!(closed.peak_backlog_mb < open_.peak_backlog_mb * 0.9)) {
    printf("  FAIL  control did not reduce the backlog "
           "(open %.2f -> closed %.2f Mb)\n", open_.peak_backlog_mb, closed.peak_backlog_mb);
    fail++;
  } else {
    printf("  pass  closed loop lowered peak backlog %.2f -> %.2f Mb (%.0f%% reduction)\n",
           open_.peak_backlog_mb, closed.peak_backlog_mb,
           100.0 * (1.0 - closed.peak_backlog_mb / open_.peak_backlog_mb));
  }

  /* 2) In open loop the backlog must really build up (there must be a problem to solve) */
  if (!(open_.peak_backlog_mb > 1.0)) {
    printf("  FAIL  almost no backlog even in open loop — the load is too light\n");
    fail++;
  } else {
    printf("  pass  backlog builds up to %.2f Mb in open loop (there is a problem to control)\n",
           open_.peak_backlog_mb);
  }

  /* 3) In a loop with delay, raising the gain must make it unstable — material for safe autonomy */
  if (!(hi.peak_backlog_mb > del.peak_backlog_mb * 1.5)) {
    printf("  FAIL  high gain did not make things worse under delay "
           "(peak %.2f vs %.2f Mb)\n", hi.peak_backlog_mb, del.peak_backlog_mb);
    fail++;
  } else {
    printf("  pass  with 500 ms delay, raising the gain 2 -> 60 moved peak backlog "
           "%.2f -> %.2f Mb (instability reproduced)\n", del.peak_backlog_mb, hi.peak_backlog_mb);
  }

  /* 4) Delay itself must also have a cost */
  if (!(del.peak_backlog_mb > closed.peak_backlog_mb)) {
    printf("  warn  delay did not degrade performance (%.2f vs %.2f Mb) — "
           "try a larger delay\n", del.peak_backlog_mb, closed.peak_backlog_mb);
  } else {
    printf("  pass  at the same gain, delay 0 -> 500 ms moved peak backlog %.2f -> %.2f Mb\n",
           closed.peak_backlog_mb, del.peak_backlog_mb);
  }

  /* 4) Determinism — same seed, same trajectory */
  printf("\n  %s\n", fail ? "Some checks FAILED." : "Plant verification passed.");
  printf("--------------------------------------------------------------\n");
  return fail;
}
