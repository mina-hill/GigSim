from evalkit.calibrate import calibrate


def test_perfect_agreement():
    assert calibrate([1, 2, 3, 4], [1, 2, 3, 4])["kappa"] == 1.0
