import io
import subprocess
import sys

from minidb.config import DatabaseConfig
from minidb.database import Database
from minidb.execution.executor import Executor
from minidb.shell import repl, run_script


def make(tmp_path):
    db = Database(DatabaseConfig(data_dir=tmp_path))
    return db, Executor(db)


def test_run_script_ok(tmp_path):
    db, ex = make(tmp_path)
    out = io.StringIO()
    assert run_script(ex, "CREATE TABLE t (a INT); INSERT INTO t VALUES (1); SELECT * FROM t;", out)
    assert out.getvalue().splitlines() == ["CREATE TABLE t", "INSERT 1", "a", "-", "1", "(1 row)"]
    db.close()


def test_syntax_error_runs_nothing(tmp_path):
    db, ex = make(tmp_path)
    out = io.StringIO()
    assert not run_script(ex, "CREATE TABLE t (a INT); SELECT FROM;", out)
    assert "^" in out.getvalue()
    assert db.table_names() == []  # the valid first statement did NOT run
    db.close()


def test_semantic_error_reported_not_raised(tmp_path):
    db, ex = make(tmp_path)
    out = io.StringIO()
    assert not run_script(ex, "SELECT * FROM nope;", out)
    assert out.getvalue().startswith("error:")
    db.close()


def test_repl_meta_and_multiline(tmp_path):
    db, ex = make(tmp_path)
    out = io.StringIO()
    repl(db, ex, io.StringIO("CREATE TABLE t (id INT NOT NULL,\n n TEXT);\n.tables\n.schema t\n.index t id\n.explain SELECT * FROM t WHERE id = 1\n.quit\nSELECT 1;\n"), out)
    lines = out.getvalue().splitlines()
    assert lines[0] == "CREATE TABLE t"
    assert "t" in lines and "t (id INT NOT NULL, n TEXT)" in lines
    assert "index on t.id created" in lines
    assert "IndexLookup(t.id = 1)" in lines
    db.close()


def test_subprocess_pipe(tmp_path):
    cfg = tmp_path / "c.toml"
    cfg.write_text(f'[database]\ndata_dir = "{(tmp_path / "d").as_posix()}"\n')
    sql = "CREATE TABLE t (a INT);\nINSERT INTO t VALUES (7);\nSELECT * FROM t;\n"
    run = subprocess.run([sys.executable, "-m", "minidb.shell", "--config", str(cfg)],
                         input=sql, capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    assert "(1 row)" in run.stdout and "7" in run.stdout
