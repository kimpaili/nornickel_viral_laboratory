from .common import evaluate_rule_effect
from .contract import LossCellData, ModuleInput, ModuleVerdict


def _fine_or_unbounded(cell: LossCellData) -> bool:
    return cell.microns_hi is None or cell.microns_hi <= 45


def evaluate(module_input: ModuleInput) -> ModuleVerdict:
    return evaluate_rule_effect(module_input, "fine_flotation", _fine_or_unbounded)
