def test_F14_cp2_needs_code() -> None:
    from app.cp_missing import answer

    assert answer() == 42
