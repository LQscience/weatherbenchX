# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Implementations of categorical metrics."""

from typing import Hashable, Mapping, Sequence, Union, Collection
import numpy as np
from weatherbenchX.metrics import base
import xarray as xr


class TruePositives(base.PerVariableStatistic):
  """True positives from binary predictions and targets."""

  @property
  def unique_name(self) -> str:
    return 'TruePositives'

  def _compute_per_variable(
      self,
      predictions: xr.DataArray,
      targets: xr.DataArray,
  ) -> xr.DataArray:

    return (predictions.astype(bool) * targets.astype(bool)).where(
        ~np.isnan(predictions * targets)
    )


class TrueNegatives(base.PerVariableStatistic):
  """True negatives from binary predictions and targets."""

  @property
  def unique_name(self) -> str:
    return 'TrueNegatives'

  def _compute_per_variable(
      self,
      predictions: xr.DataArray,
      targets: xr.DataArray,
  ) -> xr.DataArray:

    return (~predictions.astype(bool) * ~targets.astype(bool)).where(
        ~np.isnan(predictions * targets)
    )


class FalsePositives(base.PerVariableStatistic):
  """False positives from binary predictions and targets."""

  @property
  def unique_name(self) -> str:
    return 'FalsePositives'

  def _compute_per_variable(
      self,
      predictions: xr.DataArray,
      targets: xr.DataArray,
  ) -> xr.DataArray:

    return (predictions.astype(bool) * ~targets.astype(bool)).where(
        ~np.isnan(predictions * targets)
    )


class FalseNegatives(base.PerVariableStatistic):
  """False negatives from binary predictions and targets."""

  @property
  def unique_name(self) -> str:
    return 'FalseNegatives'

  def _compute_per_variable(
      self,
      predictions: xr.DataArray,
      targets: xr.DataArray,
  ) -> xr.DataArray:
    return (~predictions.astype(bool) * targets.astype(bool)).where(
        ~np.isnan(predictions * targets)
    )


class SEEPSStatistic(base.Statistic):
  """Computes SEEPS statistic. See metric class for details."""

  def __init__(
      self,
      variables: Sequence[str],
      climatology: xr.Dataset,
      dry_threshold_mm: Union[float, Sequence[float]] = 0.25,
      min_p1: Union[float, Sequence[float]] = 0.1,
      max_p1: Union[float, Sequence[float]] = 0.85,
  ):
    self._variables = variables
    self._climatology = climatology
    self._dry_threshold_mm = (
        dry_threshold_mm
        if isinstance(dry_threshold_mm, Sequence)
        else [dry_threshold_mm] * len(variables)
    )
    self._min_p1 = (
        min_p1 if isinstance(min_p1, Sequence) else [min_p1] * len(variables)
    )
    self._max_p1 = (
        max_p1 if isinstance(max_p1, Sequence) else [max_p1] * len(variables)
    )
    assert (
        len(self._variables)
        == len(self._dry_threshold_mm)
        == len(self._min_p1)
        == len(self._max_p1)
    ), 'All arguments must have the same length.'

  @property
  def unique_name(self) -> str:
    suffix = (
        '_'.join(self._variables)
        + '_dry_threshold_mm_'
        + '_'.join([str(s) for s in self._dry_threshold_mm])
        + '_min_p1_'
        + '_'.join([str(s) for s in self._min_p1])
        + '_max_p1_'
        + '_'.join([str(s) for s in self._max_p1])
    )
    return f'SEEPS_{suffix}'

  def compute(
      self,
      predictions: Mapping[Hashable, xr.DataArray],
      targets: Mapping[Hashable, xr.DataArray],
  ) -> Mapping[Hashable, xr.DataArray]:
    """Maps computation over all variables listed in self._variables."""
    out = {}
    for variable, dry_threshold_mm, min_p1, max_p1 in zip(
        self._variables, self._dry_threshold_mm, self._min_p1, self._max_p1
    ):
      out[variable] = self._compute_seeps_per_variable(
          predictions[variable],
          targets[variable],
          variable,
          dry_threshold_mm,
          min_p1,
          max_p1,
      )
    return out

  def _convert_precip_to_seeps_cat(
      self,
      da: xr.DataArray,
      wet_threshold_for_valid_time: xr.DataArray,
      dry_threshold_mm: float,
  ):
    """Helper function for SEEPS computation. Converts values to categories."""
    # Convert to SI units [meters]
    dry_threshold = dry_threshold_mm / 1000.0
    dry = da <= dry_threshold
    light = np.logical_and(
        da > dry_threshold, da < wet_threshold_for_valid_time
    )
    heavy = da >= wet_threshold_for_valid_time
    result = xr.concat(
        [dry, light, heavy],
        dim=xr.DataArray(['dry', 'light', 'heavy'], dims=['seeps_cat']),
    )
    # Convert NaNs back to NaNs. .where() will convert to float type.
    # Note that in the WB2 implementation, there was an additional
    # .astype('int') before the .where(). It seems to work fine without it
    # though.
    result = result.where(da.notnull())
    return result

  def _compute_seeps_per_variable(
      self,
      predictions: xr.DataArray,
      targets: xr.DataArray,
      variable: str,
      dry_threshold_mm: float,
      min_p1: float,
      max_p1: float,
  ) -> xr.DataArray:
    valid_time = predictions.init_time + predictions.lead_time
    wet_threshold = self._climatology[f'{variable}_seeps_threshold']
    wet_threshold_for_valid_time = wet_threshold.sel(
        dayofyear=valid_time.dt.dayofyear, hour=valid_time.dt.hour
    ).load()

    predictions_cat = self._convert_precip_to_seeps_cat(
        predictions, wet_threshold_for_valid_time, dry_threshold_mm
    )
    targets_cat = self._convert_precip_to_seeps_cat(
        targets, wet_threshold_for_valid_time, dry_threshold_mm
    )

    # Compute contingency table
    out = (
        predictions_cat.rename({'seeps_cat': 'forecast_cat'})
        * targets_cat.rename({'seeps_cat': 'truth_cat'})
    ).compute()

    p1 = (
        self._climatology[f'{variable}_seeps_dry_fraction']
        .mean(('hour', 'dayofyear'))
        .compute()
    )

    # Compute scoring matrix
    # The contingency table and p1 should have matching spatial dimensions.
    scoring_matrix = [
        [xr.zeros_like(p1), 1 / (1 - p1), 4 / (1 - p1)],
        [1 / p1, xr.zeros_like(p1), 3 / (1 - p1)],
        [
            1 / p1 + 3 / (2 + p1),
            3 / (2 + p1),
            xr.zeros_like(p1),
        ],
    ]
    das = []
    for mat in scoring_matrix:
      das.append(xr.concat(mat, dim=out.truth_cat))
    scoring_matrix = 0.5 * xr.concat(das, dim=out.forecast_cat)
    scoring_matrix = scoring_matrix.compute()

    # Take dot product
    result = xr.dot(out, scoring_matrix, dims=('forecast_cat', 'truth_cat'))

    # Mask out p1 thresholds
    mask = (p1 >= min_p1) & (p1 <= max_p1)
    result = result.where(mask, np.nan)

    # Add NaN mask. If mask coordinate already exists, combine them.
    if hasattr(predictions, 'mask'):
      if hasattr(targets, 'mask'):
        raise ValueError(
            'Both predictions and targets have masks. This should not happen.'
        )
      mask = mask & predictions.mask
    elif hasattr(targets, 'mask'):
      mask = mask & targets.mask

    result.coords['mask'] = mask

    return result


# Metrics
class CSI(base.PerVariableMetric):
  """Critical Success Index.

  Also called Threat Score (TS).

  CSI = (TP / (TP + FP + FN)).
  """

  @property
  def statistics(self) -> Mapping[Hashable, base.Statistic]:
    return {
        'TruePositives': TruePositives(),
        'FalsePositives': FalsePositives(),
        'FalseNegatives': FalseNegatives(),
    }

  def _values_from_mean_statistics_per_variable(
      self,
      statistic_values: Mapping[Hashable, xr.DataArray],
  ) -> xr.DataArray:
    """Computes metrics from aggregated statistics."""
    return statistic_values['TruePositives'] / (
        statistic_values['TruePositives']
        + statistic_values['FalsePositives']
        + statistic_values['FalseNegatives']
    )


class Accuracy(base.PerVariableMetric):
  """Accuracy.

  ACC = (TP + TN) / (TP + FP + FN + TN).
  """

  @property
  def statistics(self) -> Mapping[Hashable, base.Statistic]:
    return {
        'TruePositives': TruePositives(),
        'FalsePositives': FalsePositives(),
        'FalseNegatives': FalseNegatives(),
        'TrueNegatives': TrueNegatives(),
    }

  def _values_from_mean_statistics_per_variable(
      self,
      statistic_values: Mapping[Hashable, xr.DataArray],
  ) -> xr.DataArray:
    """Computes metrics from aggregated statistics."""
    return (
        statistic_values['TruePositives'] + statistic_values['TrueNegatives']
    ) / (
        statistic_values['TruePositives']
        + statistic_values['FalsePositives']
        + statistic_values['FalseNegatives']
        + statistic_values['TrueNegatives']
    )


class Recall(base.PerVariableMetric):
  """Also called True Positive Rate (TPR) or Sensitivity.

  Recall = TP / (TP + FN).
  """

  @property
  def statistics(self) -> Mapping[Hashable, base.Statistic]:
    return {
        'TruePositives': TruePositives(),
        'FalseNegatives': FalseNegatives(),
    }

  def _values_from_mean_statistics_per_variable(
      self,
      statistic_values: Mapping[Hashable, xr.DataArray],
  ) -> xr.DataArray:
    """Computes metrics from aggregated statistics."""
    return statistic_values['TruePositives'] / (
        statistic_values['TruePositives'] + statistic_values['FalseNegatives']
    )


class Precision(base.PerVariableMetric):
  """Also called Positive Predictive Value (PPV).

  Precision = TP / (TP + FP).
  """

  @property
  def statistics(self) -> Mapping[Hashable, base.Statistic]:
    return {
        'TruePositives': TruePositives(),
        'FalsePositives': FalsePositives(),
    }

  def _values_from_mean_statistics_per_variable(
      self,
      statistic_values: Mapping[Hashable, xr.DataArray],
  ) -> xr.DataArray:
    """Computes metrics from aggregated statistics."""
    return statistic_values['TruePositives'] / (
        statistic_values['TruePositives'] + statistic_values['FalsePositives']
    )


class F1Score(base.PerVariableMetric):
  """F1 score.

  F1 = 2 * Precision * Recall / (Precision + Recall)
     = 2 * TP / (2 * TP + FP + FN).
  """

  @property
  def statistics(self) -> Mapping[Hashable, base.Statistic]:
    return {
        'TruePositives': TruePositives(),
        'FalsePositives': FalsePositives(),
        'FalseNegatives': FalseNegatives(),
    }

  def _values_from_mean_statistics_per_variable(
      self,
      statistic_values: Mapping[Hashable, xr.DataArray],
  ) -> xr.DataArray:
    """Computes metrics from aggregated statistics."""
    return (
        2
        * statistic_values['TruePositives']
        / (
            2 * statistic_values['TruePositives']
            + statistic_values['FalsePositives']
            + statistic_values['FalseNegatives']
        )
    )


class FrequencyBias(base.PerVariableMetric):
  """Frequency bias.

  FB = PP / P = (TP + FP) / (TP + FN)
  """

  @property
  def statistics(self) -> Mapping[Hashable, base.Statistic]:
    return {
        'TruePositives': TruePositives(),
        'FalsePositives': FalsePositives(),
        'FalseNegatives': FalseNegatives(),
    }

  def _values_from_mean_statistics_per_variable(
      self,
      statistic_values: Mapping[Hashable, xr.DataArray],
  ) -> xr.DataArray:
    """Computes metrics from aggregated statistics."""
    return (
        statistic_values['TruePositives'] + statistic_values['FalsePositives']
    ) / (statistic_values['TruePositives'] + statistic_values['FalseNegatives'])


class SEEPS(base.Metric):
  """Computes Stable Equitable Error in Probability Space.

  Definition in Rodwell et al. (2010):
  https://www.ecmwf.int/en/elibrary/76205-new-equitable-score-suitable-verifying-precipitation-nwp

  Important: In most cases, the statistic will contain NaNs because of the
  masking of high and low p1 values. For this reason, a `mask` coordinate will
  be added to the resulting statistic to be used in combination with
  `masked=True` in the aggregator. If a mask already exists in either the
  predictions or targets, it will be combined with the p1 mask.
  """

  def __init__(
      self,
      variables: Sequence[str],
      climatology: xr.Dataset,
      dry_threshold_mm: Union[float, Sequence[float]] = 0.25,
      min_p1: Union[float, Sequence[float]] = 0.1,
      max_p1: Union[float, Sequence[float]] = 0.85,
  ):
    # pyformat: disable
    """Init.

    Args:
      variables: List of precipitation variables to compute SEEPS for.
      climatology: Climatology containing `*_seeps_dry_fraction` and 
        `*_seeps_threshold` for each of the precipitation variables with 
        dimensions `dayofyear` and `hour`, as well as `latitude` and `longitude`
        corresponding to the predictions/targets coordinates, see example below.
      dry_threshold_mm: Values smaller or equal are considered dry. Unit: mm. 
        Can be list for each variable. Must be same length. Default: 0.25
      min_p1: Mask out p1 values below this threshold. Can be list for each
        variable. Default: 0.1
      max_p1: Mask out p1 values above this threshold. Can be list for each
        variable. Default: 0.85

    Example:
        >>> climatology
        <xarray.Dataset> Size: 24MB
        Dimensions:                                     (hour: 4, dayofyear: 366,
                                                        longitude: 64, latitude: 32)
        Coordinates:
          * dayofyear                                   (dayofyear) int64 3kB 1 ... 366
          * hour                                        (hour) int64 32B 0 6 12 18
          * latitude                                    (latitude) float64 256B -87.1...
          * longitude                                   (longitude) float64 512B 0.0 ...
        Data variables:
            total_precipitation_6hr_seeps_dry_fraction  (hour, dayofyear, longitude, latitude) ...
            total_precipitation_6hr_seeps_threshold     (hour, dayofyear, longitude, latitude) ...
    """
    # pyformat: enable
    self._variables = variables
    self._climatology = climatology
    self._dry_threshold_mm = dry_threshold_mm
    self._min_p1 = min_p1
    self._max_p1 = max_p1

  @property
  def statistics(self) -> Mapping[Hashable, base.Statistic]:
    return {
        'SEEPSStatistic': SEEPSStatistic(
            self._variables,
            self._climatology,
            self._dry_threshold_mm,
            self._min_p1,
            self._max_p1,
        )
    }

  def _values_from_mean_statistics_with_internal_names(
      self,
      statistic_values: Mapping[str, Mapping[Hashable, xr.DataArray]],
  ) -> Mapping[Hashable, xr.DataArray]:
    return statistic_values['SEEPSStatistic']


class HSS(base.PerVariableMetric):
  """Heidke skill score (HSS), Also called Cohen’s Kappa.

  Definition in:
  Heidke, P. (1926). Berechnung Des Erfolges Und Der Güte Der Windstärkevorhersagen Im Sturmwarnungsdienst. 
      Geografiska Annaler, 8(4), 301–349. https://doi.org/10.1080/20014422.1926.11881138

  exp_correct = ((TP + FP)*(TP + FN) + (FP + TN)*(FN + TN)) / (TP + FP + FN + TN)

  HSS = ((TP + TN) - exp_correct) / (TP + FP + FN + TN - exp_correct).
  """

  @property
  def statistics(self) -> Mapping[Hashable, base.Statistic]:
    return {
        'TruePositives': TruePositives(),
        'FalsePositives': FalsePositives(),
        'FalseNegatives': FalseNegatives(),
        'TrueNegatives': TrueNegatives(),
    }

  def _values_from_mean_statistics_per_variable(
      self,
      statistic_values: Mapping[Hashable, xr.DataArray],
  ) -> xr.DataArray:
    """Computes metrics from aggregated statistics."""

    exp_correct = (
        (statistic_values['TruePositives'] + statistic_values['FalsePositives']) 
        * (statistic_values['TruePositives'] + statistic_values['FalseNegatives'])
        + (statistic_values['FalsePositives'] + statistic_values['TrueNegatives'])
        * (statistic_values['FalseNegatives'] + statistic_values['TrueNegatives'])
    ) / (
        statistic_values['TruePositives'] 
        + statistic_values['FalsePositives'] 
        + statistic_values['FalseNegatives'] 
        + statistic_values['TrueNegatives']      
    )

    return (
        statistic_values['TruePositives'] + statistic_values['TrueNegatives'] - exp_correct
    ) / (
        statistic_values['TruePositives'] 
        + statistic_values['FalsePositives'] 
        + statistic_values['FalseNegatives'] 
        + statistic_values['TrueNegatives']
        - exp_correct
    )


class GSS(base.PerVariableMetric):
  """Gilbert skill score (GSS), Also called equitable threat score.
  Definition in:
  Gilbert, G. K. (1884). Finley’s tornado predictions. American Meteorological Journal. 
      A Monthly Review of Meteorology and Allied Branches of Study (1884-1896), 1(5), 166.

  a_r = (TP + FP)*(TP + FN)/(TP + FP + FN + TN)

  HSS = (TP - a_r) / (TP + FP + FN - a_r).

  """

  @property
  def statistics(self) -> Mapping[Hashable, base.Statistic]:
    return {
        'TruePositives': TruePositives(),
        'FalsePositives': FalsePositives(),
        'FalseNegatives': FalseNegatives(),
        'TrueNegatives': TrueNegatives(),
    }

  def _values_from_mean_statistics_per_variable(
      self,
      statistic_values: Mapping[Hashable, xr.DataArray],
  ) -> xr.DataArray:
    """Computes metrics from aggregated statistics."""

    exp_correct = (
        (statistic_values['TruePositives'] + statistic_values['FalsePositives'])
        * (statistic_values['TruePositives'] + statistic_values['FalseNegatives'])
    ) / (
        statistic_values['TruePositives'] 
        + statistic_values['FalsePositives'] 
        + statistic_values['FalseNegatives'] 
        + statistic_values['TrueNegatives']      
    )

    return (
        statistic_values['TruePositives'] - exp_correct
    ) / (
        statistic_values['TruePositives'] 
        + statistic_values['FalsePositives'] 
        + statistic_values['FalseNegatives']
        - exp_correct
    )


class EDS(base.PerVariableMetric):
  """Extreme dependence score (EDS)
  
  Definition in:
  Primo, C., & Ghelli, A. (2009). The affect of the base rate on the extreme dependency score. 
      Meteorological Applications, 16(4), 533–535. https://doi.org/10.1002/met.152
  Stephenson, D. B., Casati, B., Ferro, C. A. T., & Wilson, C. A. (2008). The extreme dependency score: 
      a non‐vanishing measure for forecasts of rare events. Meteorological Applications, 15(1), 41–50. 
      https://doi.org/10.1002/met.53


  B = (TP + FN) / (TP + FP + FN + TN)
  H = TP / (TP + FN)
  
  EDS = (lnB - lnH) / (lnB + lnH)

  """

  @property
  def statistics(self) -> Mapping[Hashable, base.Statistic]:
    return {
        'TruePositives': TruePositives(),
        'FalsePositives': FalsePositives(),
        'FalseNegatives': FalseNegatives(),
        'TrueNegatives': TrueNegatives(),
    }

  def _values_from_mean_statistics_per_variable(
      self,
      statistic_values: Mapping[Hashable, xr.DataArray],
  ) -> xr.DataArray:
    """Computes metrics from aggregated statistics."""

    base_rate = (
        (statistic_values['TruePositives'] + statistic_values['FalseNegatives'])
    ) / (
        statistic_values['TruePositives'] 
        + statistic_values['FalsePositives'] 
        + statistic_values['FalseNegatives'] 
        + statistic_values['TrueNegatives']      
    )
    hit_rate = statistic_values['TruePositives'] / (
        statistic_values['TruePositives'] + statistic_values['FalseNegatives'])

    return (
      np.log(base_rate) - np.log(hit_rate)
    ) / (
       np.log(base_rate) + np.log(hit_rate)
    )


class SEDS(base.PerVariableMetric):
  """Symmetric extreme dependence score (SEDS)
  
  Definition in:
  Orozco López, E., Kaplan, D., Linhoss, A., Hogan, R. J., Ferro, C. A. T., Jolliffe, I. T., & Stephenson, D. B. (2010). 
      Equitability Revisited: Why the “Equitable Threat Score” Is Not Equitable. Weather and Forecasting, 25(2), 710–726. 
      https://doi.org/10.1175/2009WAF2222350.1


  R = (TP + FP) / (TP + FP + FN + TN)
  B = (TP + FN) / (TP + FP + FN + TN)
  H = TP / (TP + FN)
  
  EDS = (lnR - lnH) / (lnB + lnH)

  """

  @property
  def statistics(self) -> Mapping[Hashable, base.Statistic]:
    return {
        'TruePositives': TruePositives(),
        'FalsePositives': FalsePositives(),
        'FalseNegatives': FalseNegatives(),
        'TrueNegatives': TrueNegatives(),
    }

  def _values_from_mean_statistics_per_variable(
      self,
      statistic_values: Mapping[Hashable, xr.DataArray],
  ) -> xr.DataArray:
    """Computes metrics from aggregated statistics."""
    forecast_rate = (
        (statistic_values['TruePositives'] + statistic_values['FalsePositives'])
    ) / (
        statistic_values['TruePositives'] 
        + statistic_values['FalsePositives'] 
        + statistic_values['FalseNegatives'] 
        + statistic_values['TrueNegatives']      
    )
    base_rate = (
        (statistic_values['TruePositives'] + statistic_values['FalseNegatives'])
    ) / (
        statistic_values['TruePositives'] 
        + statistic_values['FalsePositives'] 
        + statistic_values['FalseNegatives'] 
        + statistic_values['TrueNegatives']      
    )
    hit_rate = statistic_values['TruePositives'] / (
        statistic_values['TruePositives'] + statistic_values['FalseNegatives'])

    return (
      np.log(forecast_rate) - np.log(hit_rate)
    ) / (
       np.log(base_rate) + np.log(hit_rate)
    )


class F(base.PerVariableMetric):
  """False alarm rate (F), Also called probability of false detection.

  Definition in:
  Donaldson, R. J., Dyer, R. M., & Kraus, M. J. (1975). An objective evaluator of techniques for predicting 
      severe weather events. In Preprints, Ninth Conf. on Severe Local Storms, Norman, OK, Amer. Meteor. Soc (Vol. 321326).


  F = FP / (FP + TN)
  """

  @property
  def statistics(self) -> Mapping[Hashable, base.Statistic]:
    return {
        'FalsePositives': FalsePositives(),
        'TrueNegatives': TrueNegatives(),
    }

  def _values_from_mean_statistics_per_variable(
      self,
      statistic_values: Mapping[Hashable, xr.DataArray],
  ) -> xr.DataArray:
    """Computes metrics from aggregated statistics."""
    return statistic_values['FalsePositives'] / (
        statistic_values['FalsePositives'] + statistic_values['TrueNegatives']
    )


class Specificity(base.PerVariableMetric):
  """Specificity, Also called true negative rate (TNR).
  Specificity = TN / (FP + TN)
  """

  @property
  def statistics(self) -> Mapping[Hashable, base.Statistic]:
    return {
        'FalsePositives': FalsePositives(),
        'TrueNegatives': TrueNegatives(),
    }

  def _values_from_mean_statistics_per_variable(
      self,
      statistic_values: Mapping[Hashable, xr.DataArray],
  ) -> xr.DataArray:
    """Computes metrics from aggregated statistics."""
    return statistic_values['TrueNegatives'] / (
        statistic_values['FalsePositives'] + statistic_values['TrueNegatives']
    )


class ORSS(base.PerVariableMetric):
  """Odds ratio skill score (ORSS), Also called Yule’s Q.

  Definition in:
  Stephenson, D. B. (2000). Use of the “Odds Ratio” for Diagnosing Forecast Skill. 
      Retrieved from https://journals.ametsoc.org/view/journals/wefo/15/2/1520-0434_2000_015_0221_uotorf_2_0_co_2.xml

  ORSS = (TP * TN - FP * FN) / (TP * TN + FP * FN)
  """

  @property
  def statistics(self) -> Mapping[Hashable, base.Statistic]:
    return {
        'TruePositives': TruePositives(),
        'FalsePositives': FalsePositives(),
        'FalseNegatives': FalseNegatives(),
        'TrueNegatives': TrueNegatives(),
    }

  def _values_from_mean_statistics_per_variable(
      self,
      statistic_values: Mapping[Hashable, xr.DataArray],
  ) -> xr.DataArray:
    """Computes metrics from aggregated statistics."""
    return (
        statistic_values['TruePositives'] * statistic_values['TrueNegatives']
        - statistic_values['FalsePositives'] * statistic_values['FalseNegatives']
    ) / (
        statistic_values['TruePositives'] * statistic_values['TrueNegatives']
        + statistic_values['FalsePositives'] * statistic_values['FalseNegatives']
    )


class PSS(base.PerVariableMetric):
  """Peirce’s skill score (PSS), Also called Hanssen and Kuipers discriminant
  
  Definition in:
  Peirce, C. S. (1884). The Numerical Measure of the Success of Predictions. Science, ns-4(93), 
      453–454. https://doi.org/10.1126/science.ns-4.93.453.b

  
  H = TP / (TP + FN)
  F = FP / (FP + TN)

  PSS = H - F = TP / (TP + FN) - FP / (FP + TN)
  """

  @property
  def statistics(self) -> Mapping[Hashable, base.Statistic]:
    return {
        'TruePositives': TruePositives(),
        'FalsePositives': FalsePositives(),
        'FalseNegatives': FalseNegatives(),
        'TrueNegatives': TrueNegatives(),
    }

  def _values_from_mean_statistics_per_variable(
      self,
      statistic_values: Mapping[Hashable, xr.DataArray],
  ) -> xr.DataArray:
    """Computes metrics from aggregated statistics."""
    return statistic_values['TruePositives'] / (
        statistic_values['TruePositives'] + statistic_values['FalseNegatives']
    ) - statistic_values['FalsePositives'] / (
        statistic_values['FalsePositives'] + statistic_values['TrueNegatives']
    )

class EDI(base.PerVariableMetric):
  """Extremal dependence index (EDI)
  
  Definition in:
  Ferro, C. A. T., & Stephenson, D. B. (2011). Extremal Dependence Indices: Improved Verification Measures 
      for Deterministic Forecasts of Rare Binary Events. Weather and Forecasting, 26(5), 699–713. 
      https://doi.org/10.1175/WAF-D-10-05030.1


  H = TP / (TP + FN)
  F = FP / (FP + TN)

  EDI = (lnF - lnH) / (lnF + lnH)

  """

  @property
  def statistics(self) -> Mapping[Hashable, base.Statistic]:
    return {
        'TruePositives': TruePositives(),
        'FalsePositives': FalsePositives(),
        'FalseNegatives': FalseNegatives(),
        'TrueNegatives': TrueNegatives(),
    }

  def _values_from_mean_statistics_per_variable(
      self,
      statistic_values: Mapping[Hashable, xr.DataArray],
  ) -> xr.DataArray:
    """Computes metrics from aggregated statistics."""

    hit_rate = statistic_values['TruePositives'] / (
        statistic_values['TruePositives'] + statistic_values['FalseNegatives'])
    false_alarm_rate = statistic_values['FalsePositives'] / (
        statistic_values['FalsePositives'] + statistic_values['TrueNegatives']
    )
    return (
      np.log(false_alarm_rate) - np.log(hit_rate)
    ) / (
       np.log(false_alarm_rate) + np.log(hit_rate)
    )


class SEDI(base.PerVariableMetric):
  """Symmetric extremal dependence index (SEDI)

  Definition in:
  Ferro, C. A. T., & Stephenson, D. B. (2011). Extremal Dependence Indices: Improved Verification Measures 
      for Deterministic Forecasts of Rare Binary Events. Weather and Forecasting, 26(5), 699–713. 
      https://doi.org/10.1175/WAF-D-10-05030.1

  H = TP / (TP + FN)
  F = FP / (FP + TN)
  
  SEDI = (lnF - lnH + ln(1-H) - ln(1-F)) / (lnF + lnH + ln(1-H) + ln(1-F))

  """

  @property
  def statistics(self) -> Mapping[Hashable, base.Statistic]:
    return {
        'TruePositives': TruePositives(),
        'FalsePositives': FalsePositives(),
        'FalseNegatives': FalseNegatives(),
        'TrueNegatives': TrueNegatives(),
    }

  def _values_from_mean_statistics_per_variable(
      self,
      statistic_values: Mapping[Hashable, xr.DataArray],
  ) -> xr.DataArray:
    """Computes metrics from aggregated statistics."""
    
    hit_rate = statistic_values['TruePositives'] / (
        statistic_values['TruePositives'] + statistic_values['FalseNegatives'])
    false_alarm_rate = statistic_values['FalsePositives'] / (
        statistic_values['FalsePositives'] + statistic_values['TrueNegatives']
    )
    return (
      np.log(false_alarm_rate) - np.log(hit_rate) + np.log(1-hit_rate) - np.log(1-false_alarm_rate)
    ) / (
       np.log(false_alarm_rate) + np.log(hit_rate) + np.log(1-hit_rate) + np.log(1-false_alarm_rate)
    )


class AUC(base.PerVariableMetric):
  """Area under receiver operating characteristic (ROC) curve (AUC)

  Definition in:
  Swets, J. A. (1986). Indices of discrimination or diagnostic accuracy: Their ROCs and implied models. 
      Psychological Bulletin, 99(1), 100–117. https://doi.org/10.1037/0033-2909.99.1.100

  H = TP / (TP + FN)
  F = FP / (FP + TN)
  
  AUC = np.trapezoid(H, F)

  """
  def __init__(
      self,
      threshold_probability_dim: str,
  ):
    """Init.

    Args:
      threshold_probability_dim: Name of dimension to use for threshold probability values
    """
    self._threshold_probability_dim = threshold_probability_dim
  
  @property
  def statistics(self) -> Mapping[Hashable, base.Statistic]:
    return {
        'TruePositives': TruePositives(),
        'FalsePositives': FalsePositives(),
        'FalseNegatives': FalseNegatives(),
        'TrueNegatives': TrueNegatives(),
    }

  def _values_from_mean_statistics_per_variable(
      self,
      statistic_values: Mapping[Hashable, xr.DataArray],
  ) -> xr.DataArray:
    """Computes metrics from aggregated statistics."""
    hit_rate = statistic_values['TruePositives'] / (
        statistic_values['TruePositives'] + statistic_values['FalseNegatives']
    )
    false_alarm_rate = statistic_values['FalsePositives'] / (
        statistic_values['FalsePositives'] + statistic_values['TrueNegatives']
    )
    auc_dims = tuple(set(hit_rate.dims) - {self._threshold_probability_dim})

    return -1 * xr.apply_ufunc(
      np.trapezoid,
      hit_rate,
      false_alarm_rate,
      input_core_dims=[hit_rate.dims, false_alarm_rate.dims],  # type: ignore
      output_core_dims=[auc_dims],
      dask="allowed",
    )
  

class ROCSS(base.PerVariableMetric):
  """Receiver operating characteristic skill score (ROCSS)

  Definition in:
  Swets, J. A., & Swets, J. A. (1986). Form of empirical ROCs in discrimination and diagnostic tasks: 
      Implications for theory and measurement of performance. Psychological Bulletin, 99(2), 181–198. 
      https://doi.org/10.1037/0033-2909.99.2.181

  H = TP / (TP + FN)
  F = FP / (FP + TN)
  
  AUC = np.trapezoid(H, F)

  ROCSS = 2 * AUC - 1

  """
  def __init__(
      self,
      threshold_probability_dim: str,
  ):
    """Init.

    Args:
      threshold_probability_dim: Name of dimension to use for threshold probability values
    """
    self._threshold_probability_dim = threshold_probability_dim
  
  @property
  def statistics(self) -> Mapping[Hashable, base.Statistic]:
    return {
        'TruePositives': TruePositives(),
        'FalsePositives': FalsePositives(),
        'FalseNegatives': FalseNegatives(),
        'TrueNegatives': TrueNegatives(),
    }

  def _values_from_mean_statistics_per_variable(
      self,
      statistic_values: Mapping[Hashable, xr.DataArray],
  ) -> xr.DataArray:
    """Computes metrics from aggregated statistics."""
    assert self._threshold_probability_dim in statistic_values['TruePositives'].dims, (
        f"Threshold probability dim {self._threshold_probability_dim} not found in "
        f"TruePositives statistic"
    )
    hit_rate = statistic_values['TruePositives'] / (
        statistic_values['TruePositives'] + statistic_values['FalseNegatives']
    )
    # print(hit_rate)
    false_alarm_rate = statistic_values['FalsePositives'] / (
        statistic_values['FalsePositives'] + statistic_values['TrueNegatives']
    )
    # print(false_alarm_rate)
    auc_dims = tuple(set(hit_rate.dims) - {self._threshold_probability_dim})

    auc = -1 * xr.apply_ufunc(
      np.trapezoid,
      hit_rate,
      false_alarm_rate,
      input_core_dims=[hit_rate.dims, false_alarm_rate.dims],  # type: ignore
      output_core_dims=[auc_dims],
      dask="allowed",
    )

    return 2 * auc - 1


class REV(base.PerVariableMetric):
  """ Relative economic value (REV)

  Definition in:
  Richardson, D. S. (2000). Skill and relative economic value of the ECMWF ensemble prediction system. 
      Quarterly Journal of the Royal Meteorological Society, 126(563), 649–667. https://doi.org/10.1002/qj.49712656313
  Richardson, David S. (2006). Predictability and economic value. In T. Palmer & R. Hagedorn (Eds.), 
      Predictability of Weather and Climate (1st ed., pp. 628–644). Cambridge University Press. https://doi.org/10.1017/CBO9780511617652.026
  Wilks, D. S. (2001). A skill score based on economic value for probability forecasts. 
      Meteorological Applications, 8(2), 209–219. https://doi.org/10.1017/S1350482701002092

  total = TP + FP + FN + TN
  e_clim = min{(TP + FN) / total, cost_loss_ratios}
  e_fcst = (TP + FP) / total * cost_loss_ratios + FN / total
  e_perfect = (TP + FN) / total * cost_loss_ratios

  REV = (e_clim - e_fcst) / (e_clim - e_perfect)
  """
  def __init__(
      self,
      cost_loss_ratios: Union[float, Sequence[float]],
  ):
    """Init.

    Args:
      cost_loss_ratios: Cost loss ratios for calculating the REV.
    """
    self._cost_loss_ratios = xr.DataArray(
        cost_loss_ratios, dims=["cost_loss_ratios"], 
        coords={"cost_loss_ratios": cost_loss_ratios}
    )
  

  @property
  def statistics(self) -> Mapping[Hashable, base.Statistic]:
    return {
        'TruePositives': TruePositives(),
        'FalsePositives': FalsePositives(),
        'FalseNegatives': FalseNegatives(),
        'TrueNegatives': TrueNegatives(),
    }

  def _values_from_mean_statistics_per_variable(
      self,
      statistic_values: Mapping[Hashable, xr.DataArray],
  ) -> xr.DataArray:
    """Computes metrics from aggregated statistics."""

    event_count = statistic_values['TruePositives'] + statistic_values['FalseNegatives']
    event_nonevent_count = event_count + statistic_values['FalsePositives'] + statistic_values['TrueNegatives']
    event_ratio = event_count / event_nonevent_count

    e_clim = event_ratio.where(event_ratio < self._cost_loss_ratios, self._cost_loss_ratios)
    e_fcst = (
        statistic_values['TruePositives'] + statistic_values['FalsePositives']
      ) / event_nonevent_count * self._cost_loss_ratios + statistic_values['FalseNegatives'] / event_nonevent_count
    e_perfect = event_ratio * self._cost_loss_ratios
    
    return (e_clim - e_fcst) / (e_clim - e_perfect)


class PREV(base.PerVariableMetric):
  """ Potential relative economic value (PREV)

  Definition in:
  Richardson, D. S. (2000). Skill and relative economic value of the ECMWF ensemble prediction system. 
      Quarterly Journal of the Royal Meteorological Society, 126(563), 649–667. https://doi.org/10.1002/qj.49712656313
  Richardson, David S. (2006). Predictability and economic value. In T. Palmer & R. Hagedorn (Eds.), 
      Predictability of Weather and Climate (1st ed., pp. 628–644). Cambridge University Press. https://doi.org/10.1017/CBO9780511617652.026
  Wilks, D. S. (2001). A skill score based on economic value for probability forecasts. 
      Meteorological Applications, 8(2), 209–219. https://doi.org/10.1017/S1350482701002092

  total = TP + FP + FN + TN
  e_clim = min{(TP + FN) / total, cost_loss_ratios}
  e_fcst = (TP + FP) / total * cost_loss_ratios + FN / total
  e_perfect = (TP + FN) / total * cost_loss_ratios

  REV = (e_clim - e_fcst) / (e_clim - e_perfect)

  PREV = max(REV) for threshold_probability in [0, 1]
  """
  def __init__(
      self,
      cost_loss_ratios: Union[float, Sequence[float]],
      threshold_probability_dim: str,
  ):
    """Init.

    Args:
      cost_loss_ratios: Cost loss ratios for calculating the REV.
    """
    self._cost_loss_ratios = xr.DataArray(
        cost_loss_ratios, dims=["cost_loss_ratios"], 
        coords={"cost_loss_ratios": cost_loss_ratios}
    )
    self._threshold_probability_dim = threshold_probability_dim
  

  @property
  def statistics(self) -> Mapping[Hashable, base.Statistic]:
    return {
        'TruePositives': TruePositives(),
        'FalsePositives': FalsePositives(),
        'FalseNegatives': FalseNegatives(),
        'TrueNegatives': TrueNegatives(),
    }

  def _values_from_mean_statistics_per_variable(
      self,
      statistic_values: Mapping[Hashable, xr.DataArray],
  ) -> xr.DataArray:
    """Computes metrics from aggregated statistics."""
    assert self._threshold_probability_dim in statistic_values['TruePositives'].dims, (
        f"Threshold probability dim {self._threshold_probability_dim} not found in "
        f"TruePositives statistic"
    )

    event_count = statistic_values['TruePositives'] + statistic_values['FalseNegatives']
    event_nonevent_count = event_count + statistic_values['FalsePositives'] + statistic_values['TrueNegatives']
    event_ratio = event_count / event_nonevent_count
    
    e_clim = event_ratio.where(event_ratio < self._cost_loss_ratios, self._cost_loss_ratios)
    e_fcst = (
        statistic_values['TruePositives'] + statistic_values['FalsePositives']
      ) / event_nonevent_count * self._cost_loss_ratios + statistic_values['FalseNegatives'] / event_nonevent_count
    e_perfect = event_ratio * self._cost_loss_ratios
    
    return ((e_clim - e_fcst) / (e_clim - e_perfect)).max(dim=self._threshold_probability_dim)


class SquaredError(base.PerVariableStatistic):
  """Squared error from binary or probabilistic predictions and targets."""

  @property
  def unique_name(self) -> str:
    return 'SquaredError'

  def _compute_per_variable(
      self,
      predictions: xr.DataArray,
      targets: xr.DataArray,
  ) -> xr.DataArray:

    return ((predictions - targets) ** 2).astype(np.float32)


class BrierScore(base.PerVariableMetric):
  """ Brier Score (BS)
  
  brier_score = mean((predictions - targets) ** 2)
  """
  def __init__(
      self,
      reduce_dims: Collection[str],
  ):
    """Init.

    Args:
      cost_loss_ratios: Cost loss ratios for calculating the REV.
    """
    self._reduce_dims = reduce_dims

  @property
  def statistics(self) -> Mapping[Hashable, base.Statistic]:
    return {
        'SquaredError': SquaredError(),
    }

  def _values_from_mean_statistics_per_variable(
      self,
      statistic_values: Mapping[Hashable, xr.DataArray],
  ) -> xr.DataArray:
    """Computes metrics from aggregated statistics."""

    reduce_dims_set = set(self._reduce_dims)
    eval_unit_dims = set(statistic_values['SquaredError'].dims)
    assert reduce_dims_set.issubset(eval_unit_dims), (
        "Can't reduce over dims that aren't present as evaluation unit dims."
    )

    return statistic_values['SquaredError'].mean(dim=reduce_dims_set)
  