from scipy.stats import norm
from statsmodels.stats.power import NormalIndPower
from statsmodels.stats.proportion import proportion_effectsize, proportions_ztest

n_needed = NormalIndPower().solve_power(proportion_effectsize(0.050, 0.054), alpha=0.05, power=0.8)
stat, p = proportions_ztest(count=[1342, 1265], nobs=[24100, 24050], alternative="larger")
print(f"needed per arm: {n_needed:.0f}, z={stat:.2f}, p={p:.3f}")
