import pandas as pd

from database.database import (
    bump_data_version,
    create_db_engine,
    get_app_settings,
    get_data_version,
    init_db,
    set_app_setting,
)
from kpi.dataset import build_ftf_config
from kpi.first_time_fix import evaluate_first_time_fix


def test_saving_repeat_window_bumps_version_and_changes_ftf_evaluation():
    engine = create_db_engine(":memory:")
    try:
        init_db(engine)
        rows = pd.DataFrame([
            {"wo": "1", "tl": "123", "asset": "Press 1", "parent_asset": None,
             "wo_type": "Reactive", "status": "Closed", "completed_by": "Tech A",
             "date_created": pd.Timestamp("2026-10-01 08:00"),
             "date_completed": pd.Timestamp("2026-10-01 09:00")},
            {"wo": "2", "tl": "123", "asset": "Press 1", "parent_asset": None,
             "wo_type": "Reactive", "status": "Closed", "completed_by": "Tech A",
             "date_created": pd.Timestamp("2026-10-05 08:00"),
             "date_completed": pd.Timestamp("2026-10-05 09:00")},
        ])
        wo_types = pd.DataFrame({"wo_type": ["Reactive"]})

        set_app_setting(engine, "ftf_window_days", 7, bump=False)
        bump_data_version(engine)
        seven_day_version = get_data_version(engine)
        seven_day_config = build_ftf_config(get_app_settings(engine), wo_types)
        seven_day_result = evaluate_first_time_fix(rows, seven_day_config).set_index("wo")
        assert not seven_day_result.loc["1", "ftf_success"]

        set_app_setting(engine, "ftf_window_days", 3, bump=False)
        bump_data_version(engine)
        three_day_config = build_ftf_config(get_app_settings(engine), wo_types)
        three_day_result = evaluate_first_time_fix(rows, three_day_config).set_index("wo")

        assert get_data_version(engine) > seven_day_version
        assert three_day_config.window_days == 3
        assert three_day_result.loc["1", "ftf_success"]
    finally:
        engine.dispose()
