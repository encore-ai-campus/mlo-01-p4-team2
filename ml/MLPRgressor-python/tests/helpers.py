from datetime import date

DAY = date(2026, 11, 11)
ID = '0197a5c0-7e2a-7c3d-9f10-2b3c4d5e6f70'


def row(**changes):
    return {**dict(kst_date=DAY, ad_group_id=ID, category=12, confirmed=True,
                   mlo_ctr=.10, mlo_cvr=.10, aio_ctr=.05, aio_cvr=.50, skn_ctr=.04, skn_cvr=0.), **changes}


def arrow_table(rows, category_type=None):
    import pyarrow as pa
    from data import FEATURES
    schema = pa.schema([('kst_date', pa.date32()), ('ad_group_id', pa.string()),
                        ('category', category_type or pa.int32()), ('confirmed', pa.bool_()),
                        *[(name, pa.float64()) for name in FEATURES[:6]],
                        ('mlo_clicks', pa.int64()), ('aio_clicks', pa.int64()), ('skn_clicks', pa.int64())])
    return pa.Table.from_pylist(rows, schema=schema)
