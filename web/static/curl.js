/*
 * cURL parser for Apitrek — pure functions, no DOM, no side effects.
 * Parses bash-style "Copy as cURL" commands (Chrome/Firefox/Edge) into the
 * pieces the test form needs: method, url, headers, body.
 *
 * Exposed as window.ApitrekCurl in the browser and via module.exports in Node
 * (tests/js runs under node --test).
 */
(function (root) {
    'use strict';

    // Header names that browsers add to every request and that add nothing to
    // an API test. Entries ending in '-' are matched as prefixes (lowercase,
    // case-insensitive); the rest as exact names.
    const BROWSER_NOISE_HEADERS = [
        'sec-ch-', 'sec-fetch-', 'sec-gpc', 'priority',
        'upgrade-insecure-requests', 'dnt', 'accept-encoding',
        'content-length', 'host',
    ];

    const SUPPORTED_METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE'];

    // Long flags (lowercased, without --) that consume the next token as value.
    const LONG_ARG_FLAGS = new Set([
        'request', 'header', 'data', 'data-raw', 'data-binary', 'data-ascii',
        'data-urlencode', 'json', 'user', 'cookie', 'user-agent', 'referer',
        'url', 'output', 'max-time', 'connect-timeout', 'write-out', 'proxy',
        'retry', 'upload-file', 'config', 'cacert', 'cert', 'key', 'range',
        'cookie-jar', 'form',
    ]);

    // Short flags that consume the next token as value.
    const SHORT_ARG_FLAGS = new Set(['o', 'm', 'w', 'x', 'T', 'K', 'r', 'c']);

    // Long flags with no value that we simply ignore.
    const LONG_IGNORE = new Set([
        'compressed', 'location', 'insecure', 'silent', 'show-error',
        'include', 'verbose', 'fail', 'globoff', 'no-buffer',
        'http1.1', 'http2', 'http3',
    ]);

    // Short flags with no value that we simply ignore (also used to detect
    // bundled flags like -sSL or -sk).
    const SHORT_IGNORE = new Set(['s', 'S', 'L', 'k', 'i', 'v', 'f', 'g', 'N']);

    const ANSI_ESCAPES = { n: '\n', t: '\t', r: '\r', "'": "'", '"': '"', '\\': '\\' };

    function looksLikeCurl(text) {
        return typeof text === 'string' && /^(\$\s*)?curl\s/i.test(text.trim());
    }

    /**
     * Split a bash-style cURL command into tokens, honoring single quotes,
     * double quotes (backslash escapes) and ANSI-C $'...' quotes. Line
     * continuations (backslash + newline) are joined into a space. Returns
     * null on an unterminated quote.
     */
    function tokenizeCurl(text) {
        if (typeof text !== 'string') return null;
        let s = text.trim().replace(/^\$\s*/, '');
        s = s.replace(/\\\r\n/g, ' ').replace(/\\\n/g, ' ');
        const tokens = [];
        let cur = null;   // null = no token started yet
        let quote = null; // "'", '"' or '$' (ANSI-C)
        for (let i = 0; i < s.length; i++) {
            const ch = s[i];
            if (quote === null) {
                if (/\s/.test(ch)) {
                    if (cur !== null) { tokens.push(cur); cur = null; }
                } else if (ch === "'" || ch === '"') {
                    if (cur === null) cur = '';
                    quote = ch;
                } else if (ch === '$' && s[i + 1] === "'" && cur === null) {
                    cur = '';
                    quote = '$';
                    i++;
                } else {
                    if (cur === null) cur = '';
                    cur += ch;
                }
            } else if (quote === "'") {
                if (ch === "'") quote = null;
                else cur += ch;
            } else if (quote === '"') {
                if (ch === '"') quote = null;
                else if (ch === '\\' && i + 1 < s.length) cur += s[++i];
                else cur += ch;
            } else { // ANSI-C $'...'
                if (ch === "'") quote = null;
                else if (ch === '\\' && i + 1 < s.length) {
                    const next = s[++i];
                    if (next === 'u' && /^[0-9a-fA-F]{4}$/.test(s.slice(i + 1, i + 5))) {
                        cur += String.fromCharCode(parseInt(s.slice(i + 1, i + 5), 16));
                        i += 4;
                    } else if (Object.prototype.hasOwnProperty.call(ANSI_ESCAPES, next)) {
                        cur += ANSI_ESCAPES[next];
                    } else {
                        cur += next;
                    }
                } else cur += ch;
            }
        }
        if (quote !== null) return null;
        if (cur !== null) tokens.push(cur);
        return tokens;
    }

    function _toBase64(str) {
        if (typeof Buffer !== 'undefined') return Buffer.from(str, 'utf8').toString('base64');
        const bytes = new TextEncoder().encode(str);
        let bin = '';
        for (const b of bytes) bin += String.fromCharCode(b);
        return btoa(bin);
    }

    function _isNoiseHeader(name) {
        const n = String(name).toLowerCase();
        return BROWSER_NOISE_HEADERS.some((h) => (h.endsWith('-') ? n.startsWith(h) : n === h));
    }

    function _isPlainObject(v) {
        return v !== null && typeof v === 'object' && !Array.isArray(v);
    }

    /**
     * Parse a bash-style cURL command into { ok, method, url, headers, body, notes }.
     * On failure returns { ok: false, error } with a user-facing message.
     */
    function parseCurl(text) {
        if (typeof text !== 'string' || !text.trim()) {
            return { ok: false, error: 'Paste a cURL command first.' };
        }
        if (/invoke-(webrequest|restmethod)/i.test(text)) {
            return { ok: false, error: "That's a PowerShell command. In devtools, choose Copy as cURL (bash) instead." };
        }
        if (text.includes('^"') || /^\s*\^\s*$/m.test(text)) {
            return { ok: false, error: 'This looks like a Windows (cmd) cURL. In devtools, choose Copy as cURL (bash) instead.' };
        }
        const tokens = tokenizeCurl(text);
        if (!tokens) {
            return { ok: false, error: "Couldn't parse that command — a quote looks unterminated." };
        }
        if (!tokens.length || tokens[0].toLowerCase() !== 'curl') {
            return { ok: false, error: "That doesn't look like a cURL command. It should start with curl." };
        }

        const notes = [];
        const ignored = new Set();
        // lower-name -> [originalName, value]; later duplicates overwrite earlier.
        const headerMap = new Map();
        const bodyParts = [];
        let url = null;
        let method = null;
        let forceGet = false;
        let head = false;

        function setHeader(name, value) {
            const lower = name.toLowerCase();
            headerMap.delete(lower);
            headerMap.set(lower, [name, value]);
        }
        function addBody(value) {
            if (value.startsWith('@')) {
                notes.push("Body from a file (@...) isn't supported — skipped.");
            } else {
                bodyParts.push(value);
            }
        }
        function takeValue(tokensArr, idxRef, inline) {
            if (inline !== undefined) return inline;
            if (idxRef.i + 1 < tokensArr.length) return tokensArr[++idxRef.i];
            return undefined;
        }

        const idxRef = { i: 1 };
        for (; idxRef.i < tokens.length; idxRef.i++) {
            const token = tokens[idxRef.i];

            if (token.startsWith('--')) {
                let name = token.slice(2);
                let inline;
                const eq = name.indexOf('=');
                if (eq !== -1) { inline = name.slice(eq + 1); name = name.slice(0, eq); }
                const lower = name.toLowerCase();

                if (LONG_ARG_FLAGS.has(lower)) {
                    const value = takeValue(tokens, idxRef, inline);
                    if (value === undefined) break;
                    if (lower === 'request') method = value.toUpperCase();
                    else if (lower === 'header') {
                        const ci = value.indexOf(':');
                        if (ci > 0) setHeader(value.slice(0, ci).trim(), value.slice(ci + 1).trim());
                    } else if (lower === 'data' || lower === 'data-raw' || lower === 'data-binary'
                        || lower === 'data-ascii' || lower === 'data-urlencode') {
                        addBody(value);
                    } else if (lower === 'json') {
                        addBody(value);
                        if (!headerMap.has('content-type')) setHeader('Content-Type', 'application/json');
                        if (!headerMap.has('accept')) setHeader('Accept', 'application/json');
                    } else if (lower === 'user') {
                        setHeader('Authorization', 'Basic ' + _toBase64(value));
                    } else if (lower === 'cookie') {
                        if (value.includes('=')) setHeader('Cookie', value);
                        else notes.push("Cookie from a file isn't supported — skipped.");
                    } else if (lower === 'user-agent') setHeader('User-Agent', value);
                    else if (lower === 'referer') setHeader('Referer', value);
                    else if (lower === 'url') url = value;
                    else if (lower === 'form') notes.push("Multipart form fields (-F) aren't supported — skipped.");
                    // remaining LONG_ARG_FLAGS are transport options: consumed and ignored
                } else if (lower === 'get') forceGet = true;
                else if (lower === 'head') head = true;
                else if (LONG_IGNORE.has(lower)) { /* ignore */ }
                else ignored.add('--' + name);
                continue;
            }

            if (token.length > 1 && token[0] === '-' && !token.startsWith('--')) {
                const flag = token.slice(1);
                if (flag.length === 1) {
                    if (flag === 'G') forceGet = true;
                    else if (flag === 'I') head = true;
                    else if (SHORT_IGNORE.has(flag)) { /* ignore */ }
                    else if (flag === 'X' || flag === 'H' || flag === 'd' || flag === 'u' || flag === 'b'
                        || flag === 'A' || flag === 'e' || flag === 'F'
                        || SHORT_ARG_FLAGS.has(flag)) {
                        const value = takeValue(tokens, idxRef, undefined);
                        if (value === undefined) break;
                        if (flag === 'X') method = value.toUpperCase();
                        else if (flag === 'H') {
                            const ci = value.indexOf(':');
                            if (ci > 0) setHeader(value.slice(0, ci).trim(), value.slice(ci + 1).trim());
                        } else if (flag === 'd') addBody(value);
                        else if (flag === 'u') setHeader('Authorization', 'Basic ' + _toBase64(value));
                        else if (flag === 'b') {
                            if (value.includes('=')) setHeader('Cookie', value);
                            else notes.push("Cookie from a file isn't supported — skipped.");
                        } else if (flag === 'A') setHeader('User-Agent', value);
                        else if (flag === 'e') setHeader('Referer', value);
                        else if (flag === 'F') notes.push("Multipart form fields (-F) aren't supported — skipped.");
                        // remaining SHORT_ARG_FLAGS are transport options: consumed and ignored
                    } else ignored.add('-' + flag);
                } else if (/^[a-zA-Z]+$/.test(flag) && flag.split('').every((f) => SHORT_IGNORE.has(f))) {
                    // bundled no-arg flags like -sSL or -sk
                } else {
                    ignored.add('-' + flag);
                }
                continue;
            }

            // First bare token that isn't a flag or flag value is the URL.
            if (url === null) url = token;
        }

        if (url === null) {
            return { ok: false, error: "Couldn't find a URL in that command." };
        }
        if (!/^https?:\/\//i.test(url)) {
            url = 'https://' + url;
            notes.push('No scheme in the URL — assumed https://.');
        }

        if (forceGet) {
            method = 'GET';
            if (bodyParts.length) {
                url += (url.includes('?') ? '&' : '?') + bodyParts.join('&');
                bodyParts.length = 0;
            }
        } else if (method === null && head) {
            method = 'HEAD';
        }

        if (ignored.size) {
            notes.push('Ignored unsupported flags: ' + Array.from(ignored).join(', ') + '.');
        }

        let body = null;
        if (bodyParts.length) {
            const raw = bodyParts.join('&');
            let parsed = null;
            let isJson = false;
            try { parsed = JSON.parse(raw); isJson = true; } catch (e) { /* not JSON */ }
            if (isJson && _isPlainObject(parsed)) {
                body = parsed;
            } else {
                notes.push('Body skipped — Apitrek currently supports JSON-object bodies only.');
            }
        }

        if (method === null) method = body !== null ? 'POST' : 'GET';
        if (!SUPPORTED_METHODS.includes(method)) {
            notes.push(`Method ${method} isn't supported — kept your current method.`);
            method = null;
        }

        const headers = {};
        let removed = 0;
        for (const [name, value] of headerMap.values()) {
            if (_isNoiseHeader(name)) { removed += 1; continue; }
            headers[name] = value;
        }
        if (removed > 0) {
            notes.push(`Removed ${removed} browser-only header${removed === 1 ? '' : 's'}.`);
        }

        return { ok: true, method, url, headers, body, notes };
    }

    /**
     * One-line summary of a successful parse, e.g.
     * "Filled method, URL, 3 headers and body from cURL."
     */
    function describeParse(result) {
        const parts = [];
        if (result.method) parts.push('method');
        parts.push('URL');
        const headerCount = result.headers ? Object.keys(result.headers).length : 0;
        if (headerCount) parts.push(`${headerCount} header${headerCount === 1 ? '' : 's'}`);
        if (result.body !== null && result.body !== undefined) parts.push('body');
        let list;
        if (parts.length === 1) list = parts[0];
        else list = parts.slice(0, -1).join(', ') + ' and ' + parts[parts.length - 1];
        return `Filled ${list} from cURL.`;
    }

    const api = { tokenizeCurl, parseCurl, looksLikeCurl, describeParse, BROWSER_NOISE_HEADERS };
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
    else root.ApitrekCurl = api;
})(typeof window !== 'undefined' ? window : globalThis);
