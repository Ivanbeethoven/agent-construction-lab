"""Small standard-library regressors; trained only on paired source outcomes."""
from dataclasses import asdict, dataclass, field
import math
from .contracts import FEATURES, MECHANISMS
from ._io import digest


class TrainingDataError(ValueError):
    pass


@dataclass
class RidgeRegressor:
    coefficients: list
    alpha: float = 2.0
    residual_std: float = 0.0
    sample_count: int = 0

    @classmethod
    def fit(cls, rows, targets, alpha=2.0):
        if not rows or len(rows) != len(targets) or alpha <= 0:
            raise ValueError("Nonempty aligned data and positive ridge penalty required")
        width = len(rows[0])
        if any(len(row) != width for row in rows) or any(not math.isfinite(float(v)) for row in rows for v in row) or any(not math.isfinite(float(v)) for v in targets):
            raise ValueError("Ridge data must be rectangular and finite")
        matrix = [[0.0] * (width + 1) for _ in range(width)]
        for row, target in zip(rows, targets):
            active = [(i, v) for i, v in enumerate(row) if v]
            for i, v in active:
                matrix[i][-1] += v * target
                for j, w in active:
                    matrix[i][j] += v * w
        for i in range(width):
            matrix[i][i] += alpha
        for i in range(width):
            pivot = max(range(i, width), key=lambda j: abs(matrix[j][i]))
            matrix[i], matrix[pivot] = matrix[pivot], matrix[i]
            divisor = matrix[i][i]
            matrix[i] = [value / divisor for value in matrix[i]]
            for j in range(width):
                if i != j:
                    scale = matrix[j][i]
                    if scale:
                        matrix[j] = [a - scale * b for a, b in zip(matrix[j], matrix[i])]
        model = cls([row[-1] for row in matrix], alpha, sample_count=len(rows))
        model.residual_std = math.sqrt(sum((model.predict(x) - y) ** 2 for x, y in zip(rows, targets)) / len(rows))
        return model

    def predict(self, values):
        if len(values) != len(self.coefficients):
            raise ValueError("Predictor feature dimension changed")
        return sum(a * b for a, b in zip(values, self.coefficients))

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        return cls(**data)


@dataclass
class PairedObservation:
    task_id: str
    family: str
    split: str
    role: str
    features: dict
    mechanisms: tuple
    execution_seed: int
    base_success: bool
    candidate_success: bool
    base_score: float
    candidate_score: float
    base_steps: int
    candidate_steps: int
    base_cost_units: float
    candidate_cost_units: float
    base_failure: str
    candidate_failure: str
    construction_seed: int = 0
    provenance: dict = field(default_factory=dict)

    def to_dict(self):
        data = asdict(self)
        data["mechanisms"] = list(self.mechanisms)
        return data

    @classmethod
    def from_dict(cls, data):
        return cls(**dict(data, mechanisms=tuple(data["mechanisms"])))


def validate_training_records(records):
    if not records:
        raise TrainingDataError("No source records")
    for row in records:
        if row.role != "source" or row.split != "source":
            raise TrainingDataError("Only source-role, source-split observations may train a constructor")
        if set(row.features) - FEATURES:
            raise TrainingDataError("Private or unknown feature")
        if row.provenance.get("base_execution_seed") != row.execution_seed or row.provenance.get("candidate_execution_seed") != row.execution_seed:
            raise TrainingDataError("Unpaired execution seeds")


def feature_vector(features):
    return [1.0, float(features.get("budget", 30)) / 30, float(features.get("workload_size", 4)) / 4,
        *[float(bool(features.get(k))) for k in ("restart_risk", "persistent_storage", "supports_idempotency", "schema_discovery")],
        float(features.get("schema_variant") == "legacy"),
        float(features.get("transient_faults") == "read"), float(features.get("transient_faults") == "write_after_commit")]


def design(features, mechanisms, failures=(), interactions=True):
    context = feature_vector(features) + list(failures)
    selected = [float(m in mechanisms) for m in MECHANISMS]
    result = list(context)
    result.extend(v * m for m in selected for v in context)
    if interactions:
        result.extend(selected[i] * selected[j] for i in range(4) for j in range(i + 1, 4))
    return result


@dataclass
class Prediction:
    uplift: float
    objective: float
    token_effect: float = 0.0
    uncertainty: float = 0.0


class ConditionalUpliftModel:
    @classmethod
    def fit(cls, records, construction_seed=17, ridge_alpha=2.0, cost_weight=0.005,
            uncertainty_penalty=0.05, include_failure=True, include_interactions=True, **kwargs):
        validate_training_records(records)
        self = cls()
        self.cost_weight, self.uncertainty_penalty = cost_weight, uncertainty_penalty
        self.include_interactions = include_interactions
        families = sorted({r.family for r in records})
        labels = sorted({r.base_failure for r in records}) if include_failure else []
        self.failures = {}
        # Out-of-family predictions prevent target leakage into the effect fit.
        out_of_fold = {id(r): [] for r in records}
        for label in labels:
            self.failures[label] = RidgeRegressor.fit([feature_vector(r.features) for r in records],
                                                     [float(r.base_failure == label) for r in records], ridge_alpha)
            for family in families:
                train = [r for r in records if r.family != family]
                if not train:
                    for r in records:
                        out_of_fold[id(r)].append(0.0)
                    break
                fitted = RidgeRegressor.fit([feature_vector(r.features) for r in train],
                                           [float(r.base_failure == label) for r in train], ridge_alpha)
                for r in records:
                    if r.family == family:
                        out_of_fold[id(r)].append(fitted.predict(feature_vector(r.features)))
        rows = [design(r.features, r.mechanisms, out_of_fold[id(r)], include_interactions) for r in records]
        self.effect = RidgeRegressor.fit(rows, [r.candidate_score - r.base_score for r in records], ridge_alpha)
        self.cost = RidgeRegressor.fit(rows, [r.candidate_steps - r.base_steps for r in records], ridge_alpha)
        self.provenance = dict(data_sha256=digest([r.to_dict() for r in records]),
            failure_crossfit="family_grouped", failure_crossfit_folds=len(families), construction_seed=construction_seed)
        return self

    def predict(self, features, mechanisms):
        if not mechanisms:
            return Prediction(0.0, 0.0)
        failures = [model.predict(feature_vector(features)) for model in self.failures.values()]
        baseline = design(features, (), failures, self.include_interactions)
        row = design(features, mechanisms, failures, self.include_interactions)
        uplift = self.effect.predict(row) - self.effect.predict(baseline)
        cost = self.cost.predict(row) - self.cost.predict(baseline)
        uncertainty = self.effect.residual_std / math.sqrt(max(1, self.effect.sample_count))
        return Prediction(uplift, uplift - self.cost_weight * cost - self.uncertainty_penalty * uncertainty, cost, uncertainty)

    def select(self, features, candidates):
        return max(candidates, key=lambda m: self.predict(features, m).objective)

    def to_dict(self):
        return dict(effect=self.effect.to_dict(), cost=self.cost.to_dict(), failures={k: v.to_dict() for k, v in self.failures.items()},
            cost_weight=self.cost_weight, uncertainty_penalty=self.uncertainty_penalty,
            include_interactions=self.include_interactions, provenance=self.provenance)

    @classmethod
    def from_dict(cls, value):
        self = cls()
        self.__dict__.update(value)
        self.effect = RidgeRegressor.from_dict(value["effect"])
        self.cost = RidgeRegressor.from_dict(value["cost"])
        self.failures = {k: RidgeRegressor.from_dict(v) for k, v in value["failures"].items()}
        return self


class ScalarFinalValueModel:
    @classmethod
    def fit(cls, records, ridge_alpha=2.0, cost_weight=0.005):
        validate_training_records(records)
        self = cls()
        self.model = RidgeRegressor.fit([design(r.features, r.mechanisms) for r in records],
            [r.candidate_score - cost_weight * r.candidate_steps for r in records], ridge_alpha)
        self.provenance = {"data_sha256": digest([r.to_dict() for r in records])}
        return self

    def select(self, features, candidates):
        return max(candidates, key=lambda m: self.model.predict(design(features, m)))


class NearestGoodEndpoint:
    @classmethod
    def fit(cls, records):
        validate_training_records(records)
        self = cls()
        self.records = [r for r in records if r.candidate_success]
        self.provenance = {"data_sha256": digest([r.to_dict() for r in records])}
        return self

    def select(self, features, candidates):
        records = [r for r in self.records if r.mechanisms in candidates]
        if not records:
            return candidates[0]
        x = feature_vector(features)
        return min(records, key=lambda r: (sum((a-b)**2 for a,b in zip(x, feature_vector(r.features))), r.candidate_steps)).mechanisms
