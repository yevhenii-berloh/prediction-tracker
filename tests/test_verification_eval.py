import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))


def test_compute_accuracy_all_correct():
    from verification_eval import compute_accuracy
    pairs = [("a", "a"), ("b", "b"), ("c", "c")]
    assert compute_accuracy(pairs) == 1.0


def test_compute_accuracy_half_correct():
    from verification_eval import compute_accuracy
    pairs = [("a", "a"), ("b", "x"), ("c", "c"), ("d", "y")]
    assert compute_accuracy(pairs) == 0.5


def test_compute_accuracy_empty_returns_zero():
    from verification_eval import compute_accuracy
    assert compute_accuracy([]) == 0.0


def test_compute_confusion_matrix_status_4_classes():
    from verification_eval import compute_confusion_matrix, STATUS_LABELS
    pairs = [
        ("confirmed", "confirmed"),
        ("confirmed", "unresolved"),
        ("refuted", "refuted"),
        ("unresolved", "unresolved"),
        ("unresolved", "premature"),
        ("premature", "premature"),
        ("premature", "premature"),
    ]
    matrix = compute_confusion_matrix(pairs, STATUS_LABELS)
    assert matrix["confirmed"]["confirmed"] == 1
    assert matrix["confirmed"]["unresolved"] == 1
    assert matrix["refuted"]["refuted"] == 1
    assert matrix["unresolved"]["unresolved"] == 1
    assert matrix["unresolved"]["premature"] == 1
    assert matrix["premature"]["premature"] == 2
    assert matrix["confirmed"]["refuted"] == 0
    assert matrix["refuted"]["confirmed"] == 0


def test_compute_confusion_matrix_strength_2_classes():
    from verification_eval import compute_confusion_matrix, STRENGTH_LABELS
    pairs = [("low", "low"), ("low", "medium"), ("medium", "medium")]
    matrix = compute_confusion_matrix(pairs, STRENGTH_LABELS)
    assert matrix["low"]["low"] == 1
    assert matrix["low"]["medium"] == 1
    assert matrix["medium"]["medium"] == 1
    assert matrix["medium"]["low"] == 0


def test_compute_confusion_matrix_skips_out_of_label_pred():
    from verification_eval import compute_confusion_matrix, STRENGTH_LABELS
    pairs = [("low", "high"), ("medium", "high")]
    matrix = compute_confusion_matrix(pairs, STRENGTH_LABELS)
    assert matrix["low"]["low"] == 0
    assert matrix["low"]["medium"] == 0
    assert matrix["medium"]["low"] == 0
    assert matrix["medium"]["medium"] == 0


def test_calibration_stats_well_calibrated():
    from verification_eval import calibration_stats
    items = [
        {"confidence": 0.9, "is_correct": True},
        {"confidence": 0.85, "is_correct": True},
        {"confidence": 0.55, "is_correct": False},
        {"confidence": 0.6, "is_correct": False},
    ]
    stats = calibration_stats(items)
    assert stats["mean_conf_correct"] == 0.875
    assert stats["mean_conf_wrong"] == 0.575
    assert round(stats["gap"], 3) == 0.300


def test_calibration_stats_no_wrong():
    from verification_eval import calibration_stats
    items = [{"confidence": 0.9, "is_correct": True}]
    stats = calibration_stats(items)
    assert stats["mean_conf_correct"] == 0.9
    assert stats["mean_conf_wrong"] is None
    assert stats["gap"] is None


def test_calibration_stats_empty():
    from verification_eval import calibration_stats
    stats = calibration_stats([])
    assert stats == {"mean_conf_correct": None, "mean_conf_wrong": None, "gap": None}
