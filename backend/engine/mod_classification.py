from .common import evaluate_rule_effect
from .contract import ModuleInput, ModuleVerdict


def evaluate(module_input: ModuleInput) -> ModuleVerdict:
    return evaluate_rule_effect(module_input, "classification")
