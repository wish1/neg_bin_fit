import os
import numpy as np
import json
from docopt import docopt
from schema import SchemaError
from scipy import stats as st
import pandas as pd

alleles = {'ref': 'alt', 'alt': 'ref'}


class ParamsHandler:
    def __init__(self, **params):
        for k, v in params.items():
            setattr(self, k, v)
        self._params = params

    def to_dict(self):
        return self._params

    def __repr__(self):
        return str(self.to_dict())

    def __len__(self):
        return len(self.to_dict())


def make_np_array_path(out, allele, line_fit=False):
    return os.path.join(out, allele + '.' + ('npy' if not line_fit else 'json'))


def get_nb_weight_path(out, allele):
    return os.path.join(out, 'NBweights_{}.tsv'.format(allele))


def check_weights_path(weights_path, line_fit):
    return weights_path, {allele: read_weights(line_fit=line_fit,
                                               np_weights_path=weights_path,
                                               allele=alleles[allele]) for allele in alleles}


def read_weights(allele, np_weights_path=None, np_weights_dict=None, line_fit=False):
    if np_weights_path:
        path = make_np_array_path(np_weights_path, allele, line_fit=line_fit)
        if not line_fit:
            np_weights = np.load(path)
        else:
            with open(path) as r:
                np_weights = json.load(r)
    elif np_weights_dict:
        np_weights = np_weights_dict[allele]
    else:
        raise AssertionError('No numpy fits provided')
    return np_weights


def get_p(BAD):
    return 1 / (BAD + 1)


def make_inferred_negative_binom_density(m, r0, p0, p, max_c, min_c):
    return make_negative_binom_density(m + r0,
                                       p * p0,
                                       1 / (1 +
                                            p ** m * ((1 - p * p0) / (1 - p0 * (1 - p))) ** (m + r0) / (1 - p) ** r0
                                            ),
                                       max_c,
                                       min_c,
                                       p2=(1 - p) * p0)


def make_negative_binom_density(r, p, w, size_of_counts, left_most, p2=None):
    if p2 is None:
        p2 = p
    negative_binom_density_array = np.zeros(size_of_counts + 1, dtype=np.float64)
    dist1 = st.nbinom(r, 1 - (1 - p2))  # 1 - p right mode
    f1 = dist1.pmf
    cdf1 = dist1.cdf
    dist2 = st.nbinom(r, 1 - p)  # p left mode
    f2 = dist2.pmf
    cdf2 = dist2.cdf
    negative_binom_norm = (cdf1(size_of_counts) -
                           (cdf1(left_most - 1) if left_most >= 1 else 0)
                           ) * w + \
                          (cdf2(size_of_counts) -
                           (cdf2(left_most - 1) if left_most >= 1 else 0)
                           ) * (1 - w)
    for k in range(left_most, size_of_counts + 1):
        negative_binom_density_array[k] = \
            (w * f1(k) + (1 - w) * f2(k)) / negative_binom_norm if negative_binom_norm != 0 else 0
    return negative_binom_density_array


def make_out_path(out, name):
    directory = os.path.join(out, name)
    if not os.path.exists(directory):
        os.mkdir(directory)
    return directory


def get_counts_dist_from_df(stats_df):
    stats_df['cover'] = stats_df['ref'] + stats_df['alt']
    return [stats_df[stats_df['cover'] == cover]['counts'].sum() for cover in range(stats_df['cover'].max())]


def init_docopt(doc, schema):
    args = docopt(doc)
    try:
        args = schema.validate(args)
    except SchemaError as e:
        print(args)
        print(doc)
        exit('Error: {}'.format(e))
    return args


def read_stats_df(filename):
    try:
        stats = pd.read_table(filename)
        assert set(stats.columns) == {'ref', 'alt', 'counts'}
        for allele in alleles:
            stats[allele] = stats[allele].astype(int)
        return stats, os.path.splitext(os.path.basename(filename))[0]
    except Exception:
        raise AssertionError


def make_cover_negative_binom_density(r, p, size_of_counts, left_most, log=False, draw_rest=False):
    negative_binom_density_array = np.zeros(size_of_counts + 1, dtype=np.float64)
    dist = st.nbinom(r, 1 - p)
    if log:
        f = dist.logpmf
    else:
        f = dist.pmf
    cdf = dist.cdf
    negative_binom_norm = cdf(size_of_counts) - (cdf(left_most - 1) if left_most >= 1 else 0)
    for k in range(0, size_of_counts + 1):
        negative_binom_density_array[k] = \
            f(k) if k >= left_most or draw_rest else (-np.inf if log else 0)
    return negative_binom_density_array - np.log(negative_binom_norm) if log else negative_binom_density_array / negative_binom_norm


def make_geom_dens(p, a, b, draw_rest=False):
    geom_density_array = np.zeros(b + 1, dtype=np.float64)
    dist = st.geom(1-p)
    f = dist.pmf
    cdf = dist.cdf
    geom_norm = cdf(b) - (cdf(a - 1) if a >= 1 else 0)
    for k in range(0, b + 1):
        geom_density_array[k] = \
            f(k) if k >= a or draw_rest else 0
    return geom_density_array / geom_norm


def combine_densities(negbin_dens, geom_dens, w, frac, p, allele_tr=5, only_negbin=False):
    comb_dens = w * geom_dens + (1 - w) * negbin_dens
    # for k in range(allele_tr * 2, len(comb_dens)):
    #     comb_dens[k] *= (1 + frac * (get_norm(p, k, allele_tr) + get_norm(1 - p, k, allele_tr)))
    if only_negbin:
        return (1 - w) * negbin_dens / comb_dens.sum()
    else:
        return comb_dens / comb_dens.sum()


def make_line_negative_binom_density(fix_c, params, p, N, allele_tr, log=True):
    neg_bin_dens1 = make_inferred_negative_binom_density(fix_c, params.r0, params.p0, p, N, allele_tr)
    neg_bin_dens2 = make_inferred_negative_binom_density(fix_c, 1, params.th0, p, N, allele_tr)
    neg_bin_dens = (1 - params.w0) * neg_bin_dens1 + params.w0 * neg_bin_dens2
    return np.log(neg_bin_dens) if log else neg_bin_dens


def stats_df_to_numpy(stats_df, min_tr, max_tr):
    rv = np.zeros([max_tr + 1, max_tr + 1], dtype=np.int_)
    for k in range(min_tr, max_tr + 1):
        for m in range(min_tr, max_tr + 1):
            slice = stats_df[(stats_df['ref'] == k) & (stats_df['alt'] == m)]
            if not slice.empty:
                rv[k, m] = slice['counts']
    return rv


def rmsea_gof(stat, df, norm):
    if norm <= 1:
        return 0
    else:
        # if max(stat - df, 0) / (df * (norm - 1)) < 0:
        #     print(stat, df)
        score = np.sqrt(max(stat - df, 0) / (df * (norm - 1)))
    return score


def calculate_gof_for_point_fit(counts_array, expected, norm, number_of_params, left_most):
    observed = counts_array.copy()
    observed[:left_most] = 0

    idxs = (observed != 0) & (expected != 0)
    if idxs.sum() <= number_of_params + 1:
        return 0
    df = idxs.sum() - 1 - number_of_params
    stat = np.sum(observed[idxs] * np.log(observed[idxs] / expected[idxs])) * 2
    return rmsea_gof(stat, df, norm)


def calculate_overall_gof(stats_df, density_func, params, main_allele, min_tr, max_tr, num_params=None):
    if num_params is None:
        num_params = len(params)
    observed = stats_df_to_numpy(stats_df, min_tr, max_tr)
    assert main_allele in ('ref', 'alt')
    if main_allele == 'alt':
        observed = observed.transpose()
    expected = np.zeros([max_tr + 1, max_tr + 1], dtype=np.int_)
    point_gofs = {}
    for fix_c in range(min_tr, max_tr + 1):
        observed_for_fix_c = observed[:, fix_c]
        norm = observed_for_fix_c.sum()
        expected[:, fix_c] = density_func(fix_c) * norm
        point_gofs[str(fix_c)] = calculate_gof_for_point_fit(observed_for_fix_c, expected[:, fix_c], norm, num_params, min_tr)
    overall_gof = calculate_gof_for_point_fit(observed.flatten(), expected.flatten(), observed.sum(), num_params, min_tr)
    return point_gofs, overall_gof
