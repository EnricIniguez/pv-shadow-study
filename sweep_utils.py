"""Validation and naming helpers for PV Butterfly shadow-study sweeps."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation


MAX_SWEEP_SCENARIOS = 100


def generate_sweep_thresholds(
    minimum: float,
    maximum: float,
    step: float,
    max_scenarios: int = MAX_SWEEP_SCENARIOS,
) -> list[float]:
    """Return an inclusive, decimal-safe sequence of GHI thresholds."""
    try:
        lower = Decimal(str(minimum))
        upper = Decimal(str(maximum))
        increment = Decimal(str(step))
    except InvalidOperation as exc:
        raise ValueError("Sweep values must be valid numbers.") from exc

    if lower < 0 or upper > 1400:
        raise ValueError("Thresholds must remain between 0 and 1,400 W/m².")
    if upper < lower:
        raise ValueError("Maximum threshold must be greater than or equal to minimum threshold.")
    if increment <= 0:
        raise ValueError("Step must be greater than zero.")

    scenario_count = int((upper - lower) // increment) + 1
    if lower + increment * (scenario_count - 1) < upper:
        scenario_count += 1
    if scenario_count > max_scenarios:
        raise ValueError(
            f"The sweep would create {scenario_count} scenarios. "
            f"Use a larger step to keep it at {max_scenarios} or fewer."
        )

    values = [lower + increment * index for index in range(scenario_count)]
    # Always include the requested maximum, even when the range is not exactly
    # divisible by the step (for example 100, 130, 160, 190, 200).
    values[-1] = upper
    return [float(value) for value in values]


def threshold_label(threshold: float) -> str:
    """Return a compact threshold label suitable for UI and file names."""
    return f"{float(threshold):g}"
