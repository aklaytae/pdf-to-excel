const http = require('http');
const fs   = require('fs');
const path = require('path');
const { execFile } = require('child_process');
const os   = require('os');

const PORT       = process.env.PORT || 3000;
const SCRIPT_DIR = __dirname;
// Detect Python path: Render uses .venv, Windows uses 'python'
const { execSync } = require('child_process');

function findPython() {
  if (process.platform === 'win32') return 'python';
  // Check .venv first (Render), then system python3
  const candidates = [
    path.join(__dirname, '.venv', 'bin', 'python3'),
    path.join(__dirname, '.venv', 'bin', 'python'),
    '/opt/render/project/src/.venv/bin/python3',
    'python3',
    'python',
  ];
  for (const p of candidates) {
    try {
      execSync(`"${p}" --version`, { stdio: 'ignore' });
      console.log(`[Python] Using: ${p}`);
      return p;
    } catch (_) {}
  }
  return 'python3';
}

const PYTHON = findPython();

// ── Multipart parser ─────────────────────────────────────────────────────────
function parseMultipart(body, boundary) {
  boundary = boundary.replace(/^"/, '').replace(/"$/, '').trim();
  const delimiter  = Buffer.from('\r\n--' + boundary);
  const firstBound = Buffer.from('--' + boundary);
  const parts = [];
  let pos = body.indexOf(firstBound);
  if (pos === -1) return parts;
  pos += firstBound.length;
  if (body[pos] === 0x0d && body[pos+1] === 0x0a) pos += 2;

  while (pos < body.length) {
    const sepIdx = body.indexOf(Buffer.from('\r\n\r\n'), pos);
    if (sepIdx === -1) break;
    const headerStr = body.slice(pos, sepIdx).toString('utf8');
    const bodyStart = sepIdx + 4;
    const nextBound = body.indexOf(delimiter, bodyStart);
    const bodyEnd   = nextBound === -1 ? body.length : nextBound;
    const nameMatch     = headerStr.match(/name="([^"]+)"/i);
    const filenameMatch = headerStr.match(/filename="([^"]+)"/i);
    parts.push({ name: nameMatch?.[1]||'', filename: filenameMatch?.[1]||'', data: body.slice(bodyStart, bodyEnd) });
    if (nextBound === -1) break;
    pos = nextBound + delimiter.length;
    if (body[pos] === 0x2d && body[pos+1] === 0x2d) break;
    if (body[pos] === 0x0d && body[pos+1] === 0x0a) pos += 2;
  }
  return parts;
}

function setCORS(res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  res.setHeader('Access-Control-Expose-Headers', 'X-Result-Info, Content-Disposition');
}

function sendError(res, code, msg) {
  setCORS(res);
  res.writeHead(code, { 'Content-Type': 'application/json; charset=utf-8' });
  res.end(JSON.stringify({ error: msg }));
}

function tryUnlink(...files) {
  for (const f of files) try { if (fs.existsSync(f)) fs.unlinkSync(f); } catch(_){}
}

// ── Detect bank type from PDF filename or content ────────────────────────────
function detectBank(filename, firstBytes) {
  const fn = (filename || '').toLowerCase();
  // BAAC signatures (UUID-style filename or contains baac/thgr)
  if (fn.includes('baac') || fn.match(/^[0-9a-f-]{36}_/)) return 'baac';
  // SCB signatures
  if (fn.includes('acctst') || fn.includes('scb') || fn.includes('siam')) return 'scb';
  // KBank signatures
  if (fn.includes('stm_sa') || fn.includes('kasikorn') || fn.includes('kbank')) return 'kbank';
  // Content heuristic (latin1 safe)
  const sample = firstBytes.slice(0, 4000).toString('latin1');
  if (sample.includes('baac.or.th') || sample.includes('BAAC') || sample.includes('49899')) return 'baac';
  if (sample.includes('KASIKORNBANK') || sample.includes('K PLUS') || sample.includes('STM_SA')) return 'kbank';
  if (sample.includes('SIAM COMMERCIAL') || sample.includes('AcctSt')) return 'scb';
  return 'ktb';
}

// ── Convert route ─────────────────────────────────────────────────────────────
function handleConvert(req, res) {
  const chunks = [];
  req.on('data', c => chunks.push(c));
  req.on('error', e => sendError(res, 500, 'Request error: ' + e.message));
  req.on('end', () => {
    const body        = Buffer.concat(chunks);
    const contentType = req.headers['content-type'] || '';
    const bmatch      = contentType.match(/boundary=(.+?)(?:;|$)/i);
    if (!bmatch) return sendError(res, 400, 'Missing boundary');

    let parts;
    try { parts = parseMultipart(body, bmatch[1]); }
    catch(e) { return sendError(res, 400, 'Multipart error: '+e.message); }

    const filePart = parts.find(p => p.filename && p.data.length > 0);
    if (!filePart) return sendError(res, 400, 'No file found');

    const ts      = Date.now();
    const tmpPdf  = path.join(os.tmpdir(), `stmt_${ts}.pdf`);
    const tmpJson = path.join(os.tmpdir(), `data_${ts}.json`);
    const tmpXlsx = path.join(os.tmpdir(), `out_${ts}.xlsx`);

    try { fs.writeFileSync(tmpPdf, filePart.data); }
    catch(e) { return sendError(res, 500, 'Cannot write PDF: '+e.message); }

    // Detect bank
    const bank       = detectBank(filePart.filename, filePart.data);
    const parseScript = bank === 'kbank'
      ? path.join(SCRIPT_DIR, 'parse_kbank.py')
      : bank === 'scb'
        ? path.join(SCRIPT_DIR, 'parse_scb.py')
        : bank === 'baac'
          ? path.join(SCRIPT_DIR, 'parse_baac.py')
          : path.join(SCRIPT_DIR, 'parse_pdf.py');

    console.log(`[/convert] Bank=${bank}  File=${filePart.filename}  Size=${filePart.data.length}`);

    // Step 1: Parse PDF
    execFile(PYTHON, [parseScript, tmpPdf],
      { maxBuffer: 50*1024*1024, timeout: 120000 },
      (err, stdout, stderr) => {
        if (err) {
          tryUnlink(tmpPdf);
          return sendError(res, 500, 'Parse failed:\n'+(stderr||err.message));
        }
        try { fs.writeFileSync(tmpJson, stdout, 'utf8'); }
        catch(e) { tryUnlink(tmpPdf); return sendError(res, 500, 'JSON write error: '+e.message); }
        try { JSON.parse(stdout); }
        catch(e) { tryUnlink(tmpPdf, tmpJson); return sendError(res, 500, 'Invalid JSON from parser'); }

        console.log(`[/convert] Parsed OK - ${stdout.length} bytes`);

        // Step 2: Generate Excel
        execFile(PYTHON, [path.join(SCRIPT_DIR, 'generate_excel.py'), tmpJson, tmpXlsx],
          { maxBuffer: 10*1024*1024, timeout: 60000 },
          (err2, stdout2, stderr2) => {
            tryUnlink(tmpPdf, tmpJson);
            if (err2) {
              return sendError(res, 500, 'Excel gen failed:\n'+(stderr2||err2.message));
            }

            let xlsxData;
            try { xlsxData = fs.readFileSync(tmpXlsx); }
            catch(e) { return sendError(res, 500, 'Cannot read XLSX: '+e.message); }
            tryUnlink(tmpXlsx);

            let result = {};
            try { result = JSON.parse(stdout2); } catch(_){}

            const origName  = (filePart.filename||'statement').replace(/\.pdf$/i,'');
            const filename  = encodeURIComponent(`${origName}_สรุปรายรับจ่าย.xlsx`);
            const resultB64 = Buffer.from(JSON.stringify(result),'utf8').toString('base64');

            setCORS(res);
            res.writeHead(200, {
              'Content-Type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
              'Content-Disposition': `attachment; filename*=UTF-8''${filename}`,
              'Content-Length': xlsxData.length,
              'X-Result-Info': resultB64,
            });
            res.end(xlsxData);
            console.log(`[/convert] Done - ${xlsxData.length} bytes`);
          }
        );
      }
    );
  });
}

// ── Server ────────────────────────────────────────────────────────────────────
const server = http.createServer((req, res) => {
  if (req.method === 'OPTIONS') { setCORS(res); res.writeHead(204); return res.end(); }

  if (req.method === 'GET' && (req.url === '/' || req.url === '/index.html')) {
    try {
      const html = fs.readFileSync(path.join(SCRIPT_DIR, 'src', 'index.html'));
      setCORS(res); res.writeHead(200, {'Content-Type':'text/html; charset=utf-8'}); return res.end(html);
    } catch(e) { return sendError(res, 500, e.message); }
  }

  if (req.method === 'POST' && req.url === '/convert') return handleConvert(req, res);

  sendError(res, 404, 'Not found');
});

server.listen(PORT, '0.0.0.0', () => {
  console.log(`\n✅  Server: http://localhost:${PORT}`);
  console.log(`    Python : ${PYTHON}`);
  console.log(`    Scripts: ${SCRIPT_DIR}\n`);
});
