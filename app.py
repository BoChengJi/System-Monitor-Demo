
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from datetime import datetime, timedelta
import os, random

from db import get_conn, CONFIG

app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app)

def init_db():
    db_type = CONFIG["db"]["type"]
    conn = get_conn()
    cur = conn.cursor()
    if db_type == "sqlite":
        cur.execute('''CREATE TABLE IF NOT EXISTS device_states (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_name TEXT NOT NULL,
            device_name TEXT NOT NULL,
            status INTEGER NOT NULL,
            ts TEXT NOT NULL
        )''')
        cur.execute('''CREATE TABLE IF NOT EXISTS important_params (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            param_name TEXT NOT NULL,
            value REAL NOT NULL,
            ts TEXT NOT NULL
        )''')
        conn.commit()
    else:  # mssql
        # device_states
        cur.execute("""IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[device_states]') AND type in (N'U'))
        BEGIN
          CREATE TABLE dbo.device_states(
            id BIGINT IDENTITY(1,1) PRIMARY KEY,
            group_name NVARCHAR(64) NOT NULL,
            device_name NVARCHAR(128) NOT NULL,
            status BIT NOT NULL,
            ts DATETIME2(0) NOT NULL
          );
          CREATE INDEX IX_ds_group_device_ts ON dbo.device_states(group_name, device_name, ts DESC);
          CREATE INDEX IX_ds_ts ON dbo.device_states(ts);
        END""")
        # important_params
        cur.execute("""IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[important_params]') AND type in (N'U'))
        BEGIN
          CREATE TABLE dbo.important_params(
            id BIGINT IDENTITY(1,1) PRIMARY KEY,
            param_name NVARCHAR(128) NOT NULL,
            value FLOAT NOT NULL,
            ts DATETIME2(0) NOT NULL
          );
          CREATE INDEX IX_ip_param_ts ON dbo.important_params(param_name, ts DESC);
          CREATE INDEX IX_ip_ts ON dbo.important_params(ts);
        END""")
        conn.commit()
    conn.close()

def table_count(conn, table):
    cur = conn.cursor()
    try:
        cur.execute(f"SELECT COUNT(1) FROM {table}")
        row = cur.fetchone()
        return row[0] if row else 0
    except Exception:
        return 0

def seed_data_if_needed():
    if not CONFIG.get("seed_on_first_run", True):
        return
    conn = get_conn()
    need = False
    if CONFIG["db"]["type"] == "sqlite":
        # SQLite：若檔案剛建立就是空的
        if table_count(conn, "device_states") == 0 and table_count(conn, "important_params") == 0:
            need = True
    else:
        # MSSQL：同樣檢查表是否為空
        if table_count(conn, "dbo.device_states") == 0 and table_count(conn, "dbo.important_params") == 0:
            need = True

    if not need:
        conn.close()
        return

    groups = CONFIG["groups"]
    now = datetime.now()
    step = timedelta(minutes=int(CONFIG.get("seed_step_minutes", 30)))
    hours = int(CONFIG.get("seed_hours", 24))

    # 產生 device_states 假資料
    rows_states = []
    ts = now - timedelta(hours=hours)
    while ts <= now:
        for g, devices in groups.items():
            for dv in devices:
                # 讓狀態緩慢變化
                state = random.choice([0,1]) if ts == now - timedelta(hours=hours) else random.choices([0,1], weights=[1,3])[0]
                rows_states.append((g, dv, state, ts))
        ts += step

    # 產生 important_params 假資料（挑幾個代表）
    param_names = CONFIG.get('params', ['Cleanroom_Temp','Cleanroom_Humid','CDA_Pressure','CH_Supply_Temp','CH_Return_Temp','CH_Flow','DI_Resistivity','VAC_Level'])
    base_val = {
        'Cleanroom_Temp': 22.0, 'Cleanroom_Humid': 48.0, 'CDA_Pressure': 7.2,
        'CH_Supply_Temp': 6.5, 'CH_Return_Temp': 13.0, 'CH_Flow': 950.0,
        'DI_Resistivity': 16.0, 'VAC_Level': -0.8
    }
    drift = {
        'Cleanroom_Temp': 0.08, 'Cleanroom_Humid': 0.5, 'CDA_Pressure': 0.05,
        'CH_Supply_Temp': 0.05, 'CH_Return_Temp': 0.06, 'CH_Flow': 12.0,
        'DI_Resistivity': 0.2, 'VAC_Level': 0.05
    }
    rows_params = []
    for p in param_names:
        val = base_val[p]
        ts = now - timedelta(hours=hours)
        while ts <= now:
            stepv = (hash(f"{p}{ts}") % 100)/100.0 - 0.5
            val += stepv * drift[p]
            rows_params.append((p, float(val), ts))
            ts += step

    # 寫入
    cur = conn.cursor()
    if CONFIG["db"]["type"] == "sqlite":
        cur.executemany("INSERT INTO device_states(group_name,device_name,status,ts) VALUES(?,?,?,?)",
                        [(g,dv,st, t.strftime('%Y-%m-%d %H:%M:%S')) for (g,dv,st,t) in rows_states])
        cur.executemany("INSERT INTO important_params(param_name,value,ts) VALUES(?,?,?)",
                        [(p,val, t.strftime('%Y-%m-%d %H:%M:%S')) for (p,val,t) in rows_params])
    else:
        cur.executemany("INSERT INTO dbo.device_states(group_name,device_name,status,ts) VALUES(?,?,?,?)",
                        rows_states)  # DATETIME2 直接寫 datetime 物件
        cur.executemany("INSERT INTO dbo.important_params(param_name,value,ts) VALUES(?,?,?)",
                        rows_params)
    conn.commit()
    conn.close()

# 啟動時初始化/塞假資料
init_db()
seed_data_if_needed()

def iso(dt: datetime) -> str:
    return dt.strftime('%Y-%m-%d %H:%M:%S')

# --------- API ---------

@app.route('/api/list/devices')
def api_list_devices():
    # 直接回傳 config.json 內的群組與設備名，確保前端能列到所有設備
    out = []
    for g, devices in CONFIG["groups"].items():
        for dv in devices:
            out.append({'group': g, 'device': dv})
    return jsonify(out)

@app.route('/api/device_states')
def api_device_states():
    db = CONFIG["db"]["type"]
    conn = get_conn(); cur = conn.cursor()
    if db == "sqlite":
        sql = '''
        SELECT ds.group_name, ds.device_name, ds.status, ds.ts
        FROM device_states ds
        JOIN (
            SELECT group_name, device_name, MAX(datetime(ts)) as max_ts
            FROM device_states GROUP BY group_name, device_name
        ) t ON t.group_name=ds.group_name AND t.device_name=ds.device_name AND datetime(ds.ts)=t.max_ts
        ORDER BY ds.group_name, ds.device_name
        '''
    else:
        sql = '''
        WITH latest AS (
          SELECT group_name, device_name, status, ts,
                 ROW_NUMBER() OVER (PARTITION BY group_name, device_name ORDER BY ts DESC) rn
          FROM dbo.device_states
        )
        SELECT group_name, device_name, status, CONVERT(varchar(19), ts, 120) as ts
        FROM latest
        WHERE rn=1
        ORDER BY group_name, device_name
        '''
    cur.execute(sql)
    rows = cur.fetchall()
    conn.close()
    # pyodbc rows are tuples; status may be True/False for BIT
    out = []
    for r in rows:
        g, dv, st, ts = r[0], r[1], int(r[2]), r[3]
        out.append({'group': g, 'device': dv, 'status': ('ON' if st==1 else 'OFF'), 'status_num': st, 'ts': ts})
    return jsonify(out)

@app.route('/api/device_state_history')
def api_device_state_history():
    group = request.args.get('group')
    start = request.args.get('from')
    end = request.args.get('to')
    hours = request.args.get('hours', type=int, default=24)

    if not start or not end:
        end_dt = datetime.now()
        start_dt = end_dt - timedelta(hours=hours)
    else:
        # datetime-local 可能是 "YYYY-MM-DD HH:MM" 或 "YYYY-MM-DDTHH:MM"
        s = start.replace('T',' ')
        e = end.replace('T',' ')
        start_dt = datetime.strptime(s, '%Y-%m-%d %H:%M')
        end_dt = datetime.strptime(e, '%Y-%m-%d %H:%M')

    db = CONFIG["db"]["type"]
    conn = get_conn(); cur = conn.cursor()
    if db == "sqlite":
        if group:
            sql = '''SELECT group_name, device_name, status, ts FROM device_states
                     WHERE group_name=? AND datetime(ts) BETWEEN datetime(?) AND datetime(?)
                     ORDER BY datetime(ts) ASC'''
            cur.execute(sql, (group, iso(start_dt), iso(end_dt)))
        else:
            sql = '''SELECT group_name, device_name, status, ts FROM device_states
                     WHERE datetime(ts) BETWEEN datetime(?) AND datetime(?)
                     ORDER BY datetime(ts) ASC'''
            cur.execute(sql, (iso(start_dt), iso(end_dt)))
    else:
        if group:
            sql = '''SELECT group_name, device_name, status, CONVERT(varchar(19), ts, 120) as ts
                     FROM dbo.device_states
                     WHERE ts BETWEEN ? AND ? AND group_name=?
                     ORDER BY ts ASC'''
            cur.execute(sql, (start_dt, end_dt, group))
        else:
            sql = '''SELECT group_name, device_name, status, CONVERT(varchar(19), ts, 120) as ts
                     FROM dbo.device_states
                     WHERE ts BETWEEN ? AND ?
                     ORDER BY ts ASC'''
            cur.execute(sql, (start_dt, end_dt))
    rows = cur.fetchall(); conn.close()
    return jsonify([{'group':r[0], 'device':r[1], 'status': ('ON' if int(r[2])==1 else 'OFF'),
                     'status_num': int(r[2]), 'ts': r[3]} for r in rows])


@app.route('/api/list/params')
def api_list_params():
    # 若 config.json 有 params，就用它；否則回傳 DB 內 distinct 值
    params = CONFIG.get("params")
    if params:
        return jsonify(params)
    db = CONFIG["db"]["type"]
    conn = get_conn(); cur = conn.cursor()
    if db == "sqlite":
        cur.execute('SELECT DISTINCT param_name FROM important_params ORDER BY param_name')
    else:
        cur.execute('SELECT DISTINCT param_name FROM dbo.important_params ORDER BY param_name')
    rows = cur.fetchall(); conn.close()
    return jsonify([r[0] for r in rows])

@app.route('/api/important_params')
def api_important_params():
    db = CONFIG["db"]["type"]
    conn = get_conn(); cur = conn.cursor()
    if db == "sqlite":
        sql = '''
        SELECT p.param_name, p.value, p.ts FROM important_params p
        JOIN (
            SELECT param_name, MAX(datetime(ts)) as max_ts
            FROM important_params GROUP BY param_name
        ) t ON t.param_name=p.param_name AND datetime(p.ts)=t.max_ts
        ORDER BY p.param_name
        '''
    else:
        sql = '''
        WITH latest AS (
          SELECT param_name, value, ts,
                 ROW_NUMBER() OVER (PARTITION BY param_name ORDER BY ts DESC) rn
          FROM dbo.important_params
        )
        SELECT param_name, value, CONVERT(varchar(19), ts, 120) as ts
        FROM latest WHERE rn=1
        ORDER BY param_name
        '''
    cur.execute(sql); rows = cur.fetchall(); conn.close()
    return jsonify([{'param':r[0], 'value': float(r[1]), 'ts': r[2]} for r in rows])

@app.route('/api/important_params_history')
def api_important_params_history():
    params = request.args.getlist('param')
    start = request.args.get('from')
    end = request.args.get('to')
    hours = request.args.get('hours', type=int, default=24)

    if not start or not end:
        end_dt = datetime.now()
        start_dt = end_dt - timedelta(hours=hours)
    else:
        s = start.replace('T',' ')
        e = end.replace('T',' ')
        start_dt = datetime.strptime(s, '%Y-%m-%d %H:%M')
        end_dt = datetime.strptime(e, '%Y-%m-%d %H:%M')

    db = CONFIG["db"]["type"]
    conn = get_conn(); cur = conn.cursor()
    if db == "sqlite":
        if params:
            qmarks = ','.join('?'*len(params))
            sql = f'''SELECT param_name, value, ts FROM important_params
                      WHERE param_name IN ({qmarks}) AND datetime(ts) BETWEEN datetime(?) AND datetime(?)
                      ORDER BY datetime(ts) ASC'''
            cur.execute(sql, (*params, iso(start_dt), iso(end_dt)))
        else:
            sql = '''SELECT param_name, value, ts FROM important_params
                     WHERE datetime(ts) BETWEEN datetime(?) AND datetime(?)
                     ORDER BY datetime(ts) ASC'''
            cur.execute(sql, (iso(start_dt), iso(end_dt)))
    else:
        if params:
            placeholders = ','.join(['?']*len(params))
            sql = f'''SELECT param_name, value, CONVERT(varchar(19), ts, 120) as ts
                      FROM dbo.important_params
                      WHERE param_name IN ({placeholders}) AND ts BETWEEN ? AND ?
                      ORDER BY ts ASC'''
            cur.execute(sql, (*params, start_dt, end_dt))
        else:
            sql = '''SELECT param_name, value, CONVERT(varchar(19), ts, 120) as ts
                      FROM dbo.important_params
                      WHERE ts BETWEEN ? AND ?
                      ORDER BY ts ASC'''
            cur.execute(sql, (start_dt, end_dt))
    rows = cur.fetchall(); conn.close()
    return jsonify([{'param':r[0], 'value': float(r[1]), 'ts': r[2]} for r in rows])

@app.route('/api/manual_state', methods=['POST'])
def manual_state():
    data = request.get_json(force=True) or {}
    g = data.get('group'); dv = data.get('device'); st = data.get('status')
    if not (g and dv and st in ('ON','OFF',1,0,'1','0','True','False')):
        return jsonify({'ok':False,'error':'group/device/status required'}), 400
    st_num = 1 if str(st).upper() in ('ON','1','TRUE','開','開機') else 0
    ts = datetime.now()
    conn = get_conn(); cur = conn.cursor()
    if CONFIG["db"]["type"] == "sqlite":
        cur.execute('INSERT INTO device_states(group_name,device_name,status,ts) VALUES(?,?,?,?)', (g,dv,st_num, ts.strftime('%Y-%m-%d %H:%M:%S')))
    else:
        cur.execute('INSERT INTO dbo.device_states(group_name,device_name,status,ts) VALUES(?,?,?,?)', (g,dv,st_num, ts))
    conn.commit(); conn.close()
    return jsonify({'ok':True, 'ts': ts.strftime('%Y-%m-%d %H:%M:%S')})

@app.route('/api/manual_param', methods=['POST'])
def manual_param():
    data = request.get_json(force=True) or {}
    p = data.get('param'); v = data.get('value')
    try:
        v = float(v)
    except Exception:
        return jsonify({'ok':False,'error':'numeric value required'}), 400
    ts = datetime.now()
    conn = get_conn(); cur = conn.cursor()
    if CONFIG["db"]["type"] == "sqlite":
        cur.execute('INSERT INTO important_params(param_name,value,ts) VALUES(?,?,?)', (p,v, ts.strftime('%Y-%m-%d %H:%M:%S')))
    else:
        cur.execute('INSERT INTO dbo.important_params(param_name,value,ts) VALUES(?,?,?)', (p,v, ts))
    conn.commit(); conn.close()
    return jsonify({'ok':True, 'ts': ts.strftime('%Y-%m-%d %H:%M:%S')})

@app.route('/')
def root():
    return send_from_directory('.', 'index.html')

@app.route('/<path:path>')
def static_files(path):
    return send_from_directory('.', path)

if __name__ == '__main__':
    app.run(debug=True)
