import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
const { parseCurl, tokenizeCurl, looksLikeCurl, describeParse } = createRequire(import.meta.url)('../../web/static/curl.js');

test('simple GET with URL only', () => {
    const r = parseCurl('curl https://api.example.com/users/1');
    assert.equal(r.ok, true);
    assert.equal(r.method, 'GET');
    assert.equal(r.url, 'https://api.example.com/users/1');
    assert.deepEqual(r.headers, {});
    assert.equal(r.body, null);
});

test('-X POST with two -H and --data-raw JSON body', () => {
    const r = parseCurl(`curl -X POST 'https://api.example.com/orders' -H 'Content-Type: application/json' -H 'Authorization: Bearer tok' --data-raw '{"a":1}'`);
    assert.equal(r.ok, true);
    assert.equal(r.method, 'POST');
    assert.equal(r.url, 'https://api.example.com/orders');
    assert.deepEqual(r.headers, { 'Content-Type': 'application/json', Authorization: 'Bearer tok' });
    assert.deepEqual(r.body, { a: 1 });
});

test('body with no -X defaults to POST', () => {
    const r = parseCurl(`curl 'https://api.example.com/x' -d '{"a":1}'`);
    assert.equal(r.method, 'POST');
});

test('multiline command with continuations parses like the one-line version', () => {
    const oneLine = parseCurl(`curl -X POST 'https://api.example.com/orders' -H 'Content-Type: application/json' --data-raw '{"a":1}'`);
    const multi = parseCurl("curl -X POST 'https://api.example.com/orders' \\\n  -H 'Content-Type: application/json' \\\n  --data-raw '{\"a\":1}'");
    assert.equal(multi.ok, true);
    assert.deepEqual(multi, oneLine);
});

test('double quotes with escaped \\" inside a JSON body', () => {
    const r = parseCurl(String.raw`curl -X POST "https://api.example.com/x" -H "Content-Type: application/json" --data-raw "{\"name\": \"a\\\"b\"}"`);
    assert.equal(r.ok, true);
    assert.deepEqual(r.body, { name: 'a"b' });
});

test("ANSI-C $'...' body with \\n and \\u00e9", () => {
    const r = parseCurl(String.raw`curl -X POST 'https://api.example.com/x' --data-raw $'{"name": "caf\u00e9"}\n'`);
    assert.equal(r.ok, true);
    assert.deepEqual(r.body, { name: 'café' });
});

test('Chrome noise headers removed with count note', () => {
    const r = parseCurl(`curl 'https://api.example.com/x' -H 'sec-ch-ua: "Chromium"' -H 'sec-fetch-mode: cors' -H 'priority: u=1' -H 'accept-encoding: gzip' -H 'Authorization: Bearer t'`);
    assert.equal(r.ok, true);
    assert.deepEqual(r.headers, { Authorization: 'Bearer t' });
    assert.ok(r.notes.some((n) => n === 'Removed 4 browser-only headers.'), r.notes.join(' | '));
});

test('-u user:pass produces Basic auth header', () => {
    const r = parseCurl(`curl -u user:pass 'https://api.example.com/x'`);
    assert.equal(r.headers.Authorization, 'Basic dXNlcjpwYXNz');
});

test("-b 'sid=abc' becomes Cookie header", () => {
    const r = parseCurl(`curl -b 'sid=abc' 'https://api.example.com/x'`);
    assert.equal(r.headers.Cookie, 'sid=abc');
});

test('-G appends body to URL as query and clears body', () => {
    const r = parseCurl(`curl -G -d 'q=shoes' 'https://api.example.com/search'`);
    assert.equal(r.method, 'GET');
    assert.equal(r.url, 'https://api.example.com/search?q=shoes');
    assert.equal(r.body, null);
});

test('form-encoded body is skipped with JSON-only note', () => {
    const r = parseCurl(`curl -X POST 'https://api.example.com/x' -d 'a=1&b=2'`);
    assert.equal(r.body, null);
    assert.ok(r.notes.some((n) => n.includes('JSON-object bodies only')), r.notes.join(' | '));
});

test('body from a file (@...) is skipped with note', () => {
    const r = parseCurl(`curl -X POST 'https://api.example.com/x' -d @payload.json`);
    assert.equal(r.body, null);
    assert.ok(r.notes.some((n) => n.includes("Body from a file (@...) isn't supported")), r.notes.join(' | '));
});

test('-I yields method null with unsupported-method note', () => {
    const r = parseCurl(`curl -I 'https://api.example.com/x'`);
    assert.equal(r.ok, true);
    assert.equal(r.method, null);
    assert.ok(r.notes.some((n) => n.includes("Method HEAD isn't supported")), r.notes.join(' | '));
});

test('-sSL --compressed -k ignored without breaking URL detection', () => {
    const r = parseCurl(`curl -sSL --compressed -k 'https://api.example.com/x'`);
    assert.equal(r.ok, true);
    assert.equal(r.method, 'GET');
    assert.equal(r.url, 'https://api.example.com/x');
});

test('no scheme gets https:// prepended with note', () => {
    const r = parseCurl('curl api.example.com/x');
    assert.equal(r.url, 'https://api.example.com/x');
    assert.ok(r.notes.some((n) => n.includes('assumed https://')));
});

test('Windows cmd format rejected with cmd error', () => {
    const r = parseCurl('curl -H ^"Accept: application/json^" https://api.example.com/x');
    assert.equal(r.ok, false);
    assert.ok(r.error.includes('Windows (cmd)'), r.error);
});

test('PowerShell rejected with PowerShell error', () => {
    const r = parseCurl('Invoke-WebRequest -Uri https://api.example.com/x');
    assert.equal(r.ok, false);
    assert.ok(r.error.includes('PowerShell'), r.error);
});

test("non-cURL input rejected", () => {
    const r = parseCurl('wget https://api.example.com/x');
    assert.equal(r.ok, false);
    assert.ok(r.error.includes("doesn't look like a cURL command"), r.error);
});

test('empty input rejected', () => {
    const r = parseCurl('   ');
    assert.equal(r.ok, false);
    assert.equal(r.error, 'Paste a cURL command first.');
});

test('unterminated quote fails gracefully without throwing', () => {
    const r = parseCurl(`curl -H 'Authorization: Bearer tok https://api.example.com/x`);
    assert.equal(r.ok, false);
    assert.ok(r.error);
});

test('looksLikeCurl detection', () => {
    assert.equal(looksLikeCurl('$ curl -X GET https://x.com'), true);
    assert.equal(looksLikeCurl('curl https://x.com'), true);
    assert.equal(looksLikeCurl('https://x.com'), false);
    assert.equal(looksLikeCurl('hello curl world'), false);
});

test('tokenizer keeps empty quoted strings', () => {
    assert.deepEqual(tokenizeCurl(`curl -H '' 'https://x.com'`), ['curl', '-H', '', 'https://x.com']);
});

test('--json sets body and JSON headers', () => {
    const r = parseCurl(`curl --json '{"a":1}' 'https://api.example.com/x'`);
    assert.deepEqual(r.body, { a: 1 });
    assert.equal(r.headers['Content-Type'], 'application/json');
    assert.equal(r.headers['Accept'], 'application/json');
    assert.equal(r.method, 'POST');
});

test('duplicate headers: later overwrites earlier', () => {
    const r = parseCurl(`curl -H 'X-Key: 1' -H 'X-Key: 2' 'https://api.example.com/x'`);
    assert.deepEqual(r.headers, { 'X-Key': '2' });
});

test('describeParse pluralizes and omits missing parts', () => {
    assert.equal(describeParse(parseCurl('curl https://api.example.com/x')), 'Filled method and URL from cURL.');
    assert.equal(
        describeParse(parseCurl(`curl -X POST 'https://api.example.com/x' -H 'A: 1' -H 'B: 2' -H 'C: 3' -d '{"a":1}'`)),
        'Filled method, URL, 3 headers and body from cURL.'
    );
});

test('unknown flags are collected into one note', () => {
    const r = parseCurl(`curl --foo --bar 'https://api.example.com/x'`);
    assert.equal(r.ok, true);
    assert.ok(r.notes.some((n) => n === 'Ignored unsupported flags: --foo, --bar.'), r.notes.join(' | '));
});

test('first-token-not-curl with $ prompt', () => {
    const r = parseCurl('$ wget https://x.com');
    assert.equal(r.ok, false);
});
