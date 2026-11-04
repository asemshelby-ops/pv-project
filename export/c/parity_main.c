/* PC test harness for the parity test (not firmware).
 *   parity <wind 0|1> <p_threshold> <persist> <input.csv> <output.csv>
 * input:  new_day,T1,T2,T3,T4,T5,ta,g,wind,doy,minute   (header line skipped)
 * output: p,alarm,f0..fN   one line per input row (p = nan while warming up)
 */
#include <stdio.h>
#include <stdlib.h>
#include "nasim.h"

void nasim_score_nowind(real *input, real *output);
void nasim_score_wind(real *input, real *output);

int main(int argc, char **argv) {
    if (argc != 6) {
        fprintf(stderr, "usage: parity wind p_threshold persist in.csv out.csv\n");
        return 2;
    }
    int use_wind = atoi(argv[1]);
    real thr = (real)strtod(argv[2], NULL);
    int persist = atoi(argv[3]);
    FILE *in = fopen(argv[4], "r"), *out = fopen(argv[5], "w");
    if (!in || !out) {
        fprintf(stderr, "cannot open files\n");
        return 1;
    }
    gbt_detector_t det;
    gbt_init(&det, use_wind ? nasim_score_wind : nasim_score_nowind, use_wind, 1, thr, persist);

    char line[1024];
    if (!fgets(line, sizeof line, in)) return 1;               /* header */
    while (fgets(line, sizeof line, in)) {
        int new_day, doy;
        double v[9];                                           /* parsed in double */
        char *p = line, *end;
        new_day = (int)strtol(p, &end, 10); p = end + 1;
        for (int i = 0; i < 8; i++) { v[i] = strtod(p, &end); p = end + 1; }
        doy = (int)strtol(p, &end, 10); p = end + 1;
        v[8] = strtod(p, &end);
        sample_t s;
        for (int i = 0; i < N_BACK; i++) s.t_back[i] = (real)v[i];
        s.ta = (real)v[5]; s.g = (real)v[6]; s.wind = (real)v[7];
        s.doy = doy; s.minute = (real)v[8];

        if (new_day) gbt_reset(&det);
        int alarm = gbt_update(&det, &s);
        fprintf(out, "%.17g,%d", (double)det.p, alarm);
        if (det.p == det.p)                                    /* features valid */
            for (int i = 0; i < det.n_features; i++) fprintf(out, ",%.17g", (double)det.f[i]);
        fprintf(out, "\n");
    }
    fclose(in);
    fclose(out);
    return 0;
}
