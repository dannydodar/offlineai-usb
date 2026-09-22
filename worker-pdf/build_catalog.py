import os, re, sqlite3, hashlib, json, traceback, io, contextlib
from pathlib import Path
from datetime import datetime, timezone

SRC = Path(os.environ.get('OFFLINEAI_PDF_ROOT', r'E:\PDF'))
OUT = Path(os.environ.get('OFFLINEAI_INDEX_DIR', str(Path(__file__).resolve().parent)))
DB = OUT / 'pdf_catalog.sqlite3'
REPORT = OUT / 'REPORT.md'
INDEX = SRC / 'index.md'

def utc_now():
    return datetime.now(timezone.utc).isoformat()

def title_for(path, meta):
    t = (meta or {}).get('/Title') or ''
    t = re.sub(r'\s+', ' ', str(t)).strip()
    return t or path.stem

def category_for(path):
    try:
        rel = path.relative_to(SRC)
        parts = rel.parts
        return parts[0] if len(parts) > 1 else '(root)'
    except ValueError:
        return '(outside source)'

def file_hash(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()

def init_db(c):
    c.executescript('''
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS documents (
      id INTEGER PRIMARY KEY, rel_path TEXT UNIQUE NOT NULL, abs_path TEXT NOT NULL,
      title TEXT, category TEXT, page_count INTEGER, file_size INTEGER,
      modified_ns INTEGER, sha256 TEXT, extraction_status TEXT, scanned_likely INTEGER,
      text_chars INTEGER, error TEXT, processed_at TEXT
    );
    CREATE TABLE IF NOT EXISTS pages (
      document_id INTEGER NOT NULL, page_no INTEGER NOT NULL, text TEXT,
      chars INTEGER, extraction_error TEXT, PRIMARY KEY(document_id,page_no),
      FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE
    );
    CREATE VIRTUAL TABLE IF NOT EXISTS page_fts USING fts5(rel_path, title, category, page_no UNINDEXED, text);
    CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
    ''')

def process_one(c, path):
    rel = str(path.relative_to(SRC)).replace('\\','/')
    st = path.stat()
    row = c.execute('SELECT id, file_size, modified_ns, sha256 FROM documents WHERE rel_path=?', (rel,)).fetchone()
    # Hash is only needed for changed metadata; this makes reruns cheap.
    if row and row[1] == st.st_size and row[2] == st.st_mtime_ns:
        return 'skipped'
    sha = file_hash(path)
    if row and row[3] == sha:
        c.execute('UPDATE documents SET file_size=?, modified_ns=?, processed_at=? WHERE id=?', (st.st_size, st.st_mtime_ns, utc_now(), row[0]))
        return 'skipped'
    doc_id = row[0] if row else None
    try:
        with path.open('rb') as raw:
            header = raw.read(5)
        if header != b'%PDF-':
            raise ValueError(f'file does not start with PDF header (got {header!r})')
        from pypdf import PdfReader
        with contextlib.redirect_stderr(io.StringIO()):
            reader = PdfReader(str(path), strict=False)
        page_count = len(reader.pages)
        meta = reader.metadata or {}
        title = title_for(path, meta)
        category = category_for(path)
        texts, errors, chars, nonempty = [], [], 0, 0
        for i, page in enumerate(reader.pages, 1):
            try:
                text = page.extract_text() or ''
                text = re.sub(r'\s+', ' ', text).strip()
                if text: nonempty += 1
                chars += len(text)
                texts.append((i, text, len(text), None))
            except Exception as e:
                msg = f'{type(e).__name__}: {e}'
                errors.append(f'page {i}: {msg}')
                texts.append((i, '', 0, msg))
        scanned = int(page_count > 0 and nonempty == 0)
        status = 'error' if errors and not nonempty else ('partial-error' if errors else ('scanned-image-only' if scanned else 'ok'))
        err = '; '.join(errors)[:4000] if errors else None
    except Exception as e:
        page_count, title, category, texts, chars, scanned = None, path.stem, category_for(path), [], 0, 1
        status, err = 'unreadable', f'{type(e).__name__}: {e}\n{traceback.format_exc(limit=2)}'[:4000]
    if doc_id is None:
        cur = c.execute('''INSERT INTO documents(rel_path,abs_path,title,category,page_count,file_size,modified_ns,sha256,extraction_status,scanned_likely,text_chars,error,processed_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''', (rel,str(path),title,category,page_count,st.st_size,st.st_mtime_ns,sha,status,scanned,chars,err,utc_now()))
        doc_id = cur.lastrowid
    else:
        c.execute('''UPDATE documents SET abs_path=?,title=?,category=?,page_count=?,file_size=?,modified_ns=?,sha256=?,extraction_status=?,scanned_likely=?,text_chars=?,error=?,processed_at=? WHERE id=?''',
          (str(path),title,category,page_count,st.st_size,st.st_mtime_ns,sha,status,scanned,chars,err,utc_now(),doc_id))
        c.execute('DELETE FROM pages WHERE document_id=?', (doc_id,))
        c.execute('DELETE FROM page_fts WHERE rowid IN (SELECT rowid FROM page_fts WHERE rel_path=?)', (rel,))
    for i, text, n, pe in texts:
        c.execute('INSERT INTO pages(document_id,page_no,text,chars,extraction_error) VALUES(?,?,?,?,?)', (doc_id,i,text,n,pe))
        c.execute('INSERT INTO page_fts(rel_path,title,category,page_no,text) VALUES(?,?,?,?,?)', (rel,title,category,i,text))
    return status

def write_index(c):
    rows = c.execute('SELECT rel_path,title,category,page_count,file_size,modified_ns,extraction_status,scanned_likely,error FROM documents ORDER BY rel_path COLLATE NOCASE').fetchall()
    lines = ['# PDF Catalog', '', f'Generated: {utc_now()}', '', '| Relative path | Title | Category | Pages | Size (bytes) | Modified (UTC) | Text status | Scanned likely | Errors |', '|---|---|---|---:|---:|---|---|---:|---|']
    for rel,title,cat,pages,size,ns,status,scanned,err in rows:
        mod = datetime.fromtimestamp(ns/1e9, timezone.utc).isoformat() if ns else ''
        clean = lambda x: str(x or '').replace('|','\\|').replace('\n',' ')
        lines.append(f'| {clean(rel)} | {clean(title)} | {clean(cat)} | {pages or ""} | {size or ""} | {mod} | {status} | {scanned} | {clean(err)} |')
    INDEX.write_text('\n'.join(lines)+'\n', encoding='utf-8')

def write_report(c, started, finished, processed, skipped):
    total = c.execute('SELECT COUNT(*) FROM documents').fetchone()[0]
    pages = c.execute('SELECT COALESCE(SUM(page_count),0) FROM documents').fetchone()[0]
    scanned = c.execute('SELECT COUNT(*) FROM documents WHERE scanned_likely=1').fetchone()[0]
    unread = c.execute("SELECT COUNT(*) FROM documents WHERE extraction_status='unreadable'").fetchone()[0]
    errors = c.execute("SELECT COUNT(*) FROM documents WHERE extraction_status IN ('error','partial-error','unreadable')").fetchone()[0]
    lines = [f'# PDF Worker Report', '', f'- Database: `{DB}`', f'- Source: `{SRC}`', f'- Human-readable catalog: `{INDEX}`', f'- Run started: {started}', f'- Run finished: {finished}', f'- Documents cataloged: {total}', f'- Total pages reported: {pages}', f'- Processed this run: {processed}', f'- Skipped unchanged: {skipped}', f'- Likely scanned/image-only: {scanned}', f'- Unreadable: {unread}', f'- Documents with extraction errors: {errors}', '', 'The database contains `documents`, `pages`, and the self-contained FTS5 virtual table `page_fts`, which returns matching paths, titles, page numbers, and text. Reruns compare size and modification time, then SHA-256 when needed, and reprocess only changed/new PDFs. Original PDFs are read-only inputs and were not copied or modified.', '']
    REPORT.write_text('\n'.join(lines), encoding='utf-8')

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    started = utc_now()
    paths = sorted(SRC.rglob('*.pdf'), key=lambda p: str(p).lower())
    c = sqlite3.connect(DB)
    try:
        init_db(c); c.commit()
        processed = skipped = 0
        for idx, path in enumerate(paths, 1):
            result = process_one(c, path)
            if result == 'skipped': skipped += 1
            else: processed += 1
            c.commit()
            if idx % 25 == 0: print(f'{idx}/{len(paths)} processed={processed} skipped={skipped}', flush=True)
        c.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('last_run',?)", (utc_now(),)); c.commit()
        write_index(c)
        write_report(c, started, utc_now(), processed, skipped)
        print(json.dumps({'db':str(DB),'index':str(INDEX),'report':str(REPORT),'files':len(paths),'processed':processed,'skipped':skipped}))
    finally:
        c.close()

if __name__ == '__main__': main()
