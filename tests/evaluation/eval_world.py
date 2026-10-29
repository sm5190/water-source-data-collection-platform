"""A persisted, review-enabled research run of the mocked Toronto world (no network, no LLM),
plus a matching reference workbook, for end-to-end evaluation tests."""

from pathlib import Path
from acquisition_helpers import make_acquisition
from eval_helpers import row, write_workbook
from review_helpers import ANSWERS, World
from slice_helpers import (
    DOWN_URL, PDF_URL, UTIL_URL, ModelScript, make_discovery, make_gateway, make_site,
)
from water_source_research.agents.graph import build_graph
from water_source_research.context_manager import ContextBundleStore, ContextService
from water_source_research.evaluation.reference import import_reference
from water_source_research.evaluation.store import EvaluationStore

W = "total_city_water_withdrawals_m3_day"

class EvalWorld(World):
    """review_helpers.World with persisted context bundles (the evaluator reads them)."""

    def graph(self):
        gateway, _ = make_gateway(ModelScript(answers=ANSWERS), structured_max_attempts=1)
        return build_graph(gateway=gateway, discovery=make_discovery(),
                           acquisition=make_acquisition(make_site()), max_discovery_rounds=1,
                           evidence_store=self.evidence, review_store=self.reviews,
                           record_store=self.records, checkpointer=self.checkpoints,
                           context_service=ContextService(store=ContextBundleStore(self.engine)))

def reference_row(**changes):
    """What a researcher recorded for the mocked Toronto (980,000 m3/d in 2025, Lake Ontario,
    Toronto Water, Surface), with sources per information label."""
    refs = (f"Water Sources: City of Toronto. (n.d.). Tap water. {UTIL_URL}; "
            f"Water Withdrawal: Toronto Water. (2024). Annual report. {PDF_URL}; "
            f"Water Quality: Toronto Water. (2024). Quality. https://www.toronto.ca/quality-2024/; "
            f"Infrastructure: Other. (2020). Intakes. {DOWN_URL}")
    return row(total_city_water_withdrawals_m3_day=980000.0,
               total_city_water_withdrawals_year=2025.0,
               source_withdrawal_contribution_m3_day=980000.0,
               source_withdrawal_contribution_year=2025.0,
               city_population_millions=2.794, utility_reported_city_population_millions=3.6,
               population_served_millions=2.794, percentage_total_population=100.0,
               references=refs, **changes)

def import_world_reference(tmp_path: Path, engine, **changes) -> str:
    ds = import_reference(write_workbook(tmp_path / "reference.xlsx",
                                         [reference_row(**changes)]), ["Toronto"])
    EvaluationStore(engine).save_dataset(ds)
    return ds.version
