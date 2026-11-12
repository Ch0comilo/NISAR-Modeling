"""Public API v2 request/response models, also used by OpenAPI."""
from datetime import date
from hashlib import sha256
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Longitude = Annotated[float, Field(ge=-180, le=180, allow_inf_nan=False)]
Latitude = Annotated[float, Field(ge=-90, le=90, allow_inf_nan=False)]
SeriesKey = Annotated[str, Field(pattern=r"^\d{3}_[AD]_\d{3}_\d{4}_[A-Z0-9]{2,4}$")]
PairID = Annotated[str, Field(pattern=r"^NISAR_L2_[A-Za-z0-9_]{1,170}$")]
Hazard = Literal["wildfire", "flood", "deforestation", "earthquake"]
Lens = Literal["flood_wetland", "vegetation_disturbance", "wildfire", "agriculture"]
VisualizationRecipe = Literal["polarization_rgb", "cross_co_ratio", "cross_co_ratio_change",
                              "three_date_rgb", "temporal_median", "temporal_mad"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CoverageQuery(Contract):
    longitude: Longitude
    latitude: Latitude
    start_date: date
    end_date: date
    product: Literal["GCOV", "GUNW"] = "GCOV"

    @model_validator(mode="after")
    def bounded_window(self):
        if not 0 <= (self.end_date - self.start_date).days <= 366:
            raise ValueError("Use an increasing UTC date window of at most 366 days.")
        return self


class JobRequest(CoverageQuery):
    operation: Literal["explore", "analysis", "characterization", "flood", "fingerprint",
                       "insar-pair", "insar-stack", "insar-readiness", "insar-network"]
    series_key: SeriesKey | None = None
    compare_dates: tuple[date, date] | None = None
    baseline_end_date: date | None = None
    lens: Lens | None = None
    optical_evidence: bool = False
    pair_id: PairID | None = None
    coherence_threshold: Annotated[float, Field(ge=.1, le=.9, allow_inf_nan=False)] = .35
    analyses: Annotated[list[Literal["physical", "spectral", "stochastic", "transformer"]],
                        Field(min_length=1, max_length=4)] = Field(default_factory=lambda: ["physical"])

    @model_validator(mode="after")
    def operation_fields(self):
        fields = self.model_fields_set
        if self.compare_dates:
            before, after = self.compare_dates
            if self.operation != "explore" or not self.start_date <= before < after <= self.end_date:
                raise ValueError("Use two increasing explore comparison dates within the window.")
        if (self.operation == "insar-pair") != bool(self.pair_id):
            raise ValueError("Exactly insar-pair requires a catalog pair_id.")
        if self.operation != "explore" and fields & {"lens", "optical_evidence"}:
            raise ValueError("Lens and optical evidence options apply to explore.")
        if self.optical_evidence and self.lens not in {"flood_wetland", "vegetation_disturbance", "wildfire"}:
            raise ValueError("Optical evidence requires a supported optical lens.")
        if self.baseline_end_date and not self.start_date <= self.baseline_end_date < (
            self.compare_dates[1] if self.compare_dates else self.end_date
        ):
            raise ValueError("The baseline cutoff must be inside the window before assessment.")
        if self.operation in {"characterization", "flood", "fingerprint"} and not self.baseline_end_date:
            raise ValueError("Supply a baseline cutoff to split baseline and assessment.")
        if self.operation == "explore" and self.baseline_end_date and not self.lens:
            raise ValueError("The explore baseline cutoff requires a lens.")
        if "analyses" in fields and self.operation != "analysis":
            raise ValueError("Analyses apply to the analysis operation.")
        if self.operation.startswith("insar") and "product" in fields and self.product != "GUNW":
            raise ValueError("InSAR uses GUNW.")
        if not self.operation.startswith("insar") and self.product != "GCOV":
            raise ValueError("Backscatter analysis uses GCOV.")
        if self.series_key and self.operation not in {"explore", "analysis", "insar-stack", "insar-readiness"}:
            raise ValueError("This operation does not accept a series selector.")
        return self


class ApiError(Contract):
    message: str
    code: str


class Job(Contract):
    api_version: Literal["2.0"] = "2.0"
    job_id: str
    status: Literal["queued", "running", "succeeded", "failed"]
    status_url: str
    cache: Literal["miss", "in_flight", "hit"] = "miss"
    result: dict | None = None
    error: ApiError | None = None


class Grid(Contract):
    crs: str
    bounds: tuple[float, float, float, float]
    shape: tuple[int, int]
    posting_m: tuple[float, float]
    north_up: Literal[True] = True


class Layer(Contract):
    id: str
    png_url: str
    cog_url: str
    bounds: tuple[float, float, float, float]
    crs: Literal["EPSG:4326"] = "EPSG:4326"
    north_up: Literal[True] = True
    units: str
    colormap: str
    range: tuple[float, float]
    legend: dict[str, str] | None = None


class VisualizationComponent(Contract):
    band: str
    label: str
    polarizations: list[str]
    display_db: tuple[float, float]


class Visualization(Contract):
    id: str
    recipe: VisualizationRecipe
    title: str
    dates: list[date]
    interpretation: str
    components: list[VisualizationComponent]
    colormap: str
    supported_cells: int
    total_cells: int
    grid: Grid
    bounds: tuple[float, float, float, float]
    crs: Literal["EPSG:4326"] = "EPSG:4326"
    png_url: str
    cog_url: str


class Case(Contract):
    api_version: Literal["2.0"] = "2.0"
    id: str
    title: str
    point: tuple[Longitude, Latitude]
    grid: Grid
    series_key: str
    baseline_dates: list[date]
    assessment_dates: list[date]
    status: str
    evidence: dict
    layers: list[Layer]
    visualizations: list[Visualization] = Field(default_factory=list)
    series_url: str
    isDemo: Literal[False] = False


class LegacyEvidence(Contract):
    api_version: Literal["2.0"] = "2.0"
    kind: Literal["change", "deformation"]
    hazard: Hazard
    track: int
    frame: int
    before: date
    after: date
    isDemo: Literal[False] = False
    affectedAreaHa: None = None
    severityHa: None = None
    maxTowardCm: None = None
    maxAwayCm: None = None
    deformedAreaKm2: None = None
    los_displacement_cm: None = None
    evidence: dict
    layers: list[Layer]
    case_url: str


def canonical_job(request: JobRequest, revision: str) -> tuple[str, dict]:
    payload = request.model_dump(mode="json", exclude_none=True)
    # This tile center is the measured point, so the same key cannot represent different reads.
    for axis in ("longitude", "latitude"):
        snapped = round(payload[axis], 3)
        payload[axis] = snapped if snapped else 0.0
    payload["analyses"] = sorted(set(payload["analyses"]))
    payload["product"] = "GUNW" if request.operation.startswith("insar") else "GCOV"
    identity = json.dumps({"version": "2.0", "revision": revision, "request": payload},
                          sort_keys=True, separators=(",", ":"), allow_nan=False)
    return sha256(identity.encode()).hexdigest(), payload
