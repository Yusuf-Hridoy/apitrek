document.addEventListener('DOMContentLoaded', () => {
    const form = document.getElementById('testForm');
    const submitBtn = document.getElementById('submitBtn');
    const btnText = submitBtn.querySelector('.btn-text');
    const spinner = submitBtn.querySelector('.spinner');
    const errorSection = document.getElementById('errorSection');
    const errorMessage = document.getElementById('errorMessage');
    const resultsSection = document.getElementById('resultsSection');
    const copyBtn = document.getElementById('copyBtn');
    const exportBtn = document.getElementById('exportBtn');
    const autoFetchCheckbox = document.getElementById('autoFetch');
    const sampleResponseGroup = document.getElementById('sampleResponseGroup');

    const lists = {
        positive: document.getElementById('positiveList'),
        negative: document.getElementById('negativeList'),
        edge: document.getElementById('edgeList'),
        assertions: document.getElementById('assertionsList'),
    };

    let lastResult = null;
    let lastSessionId = null;
    let caseCards = [];

    // Trigger a browser download for a Blob.
    function triggerDownload(blob, filename) {
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
        window.URL.revokeObjectURL(url);
    }

    // POST JSON to an endpoint that returns a file, then download the response.
    async function downloadFromApi(url, body, filename, successMsg) {
        try {
            const res = await fetch(url, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            if (!res.ok) {
                const data = await res.json().catch(() => ({}));
                showError(data.detail || `Export failed (${res.status})`);
                return;
            }
            triggerDownload(await res.blob(), filename);
            showToast(successMsg, 'success');
        } catch (err) {
            showError('Export failed. Please make sure the server is running.');
        }
    }

    if (autoFetchCheckbox && sampleResponseGroup) {
        autoFetchCheckbox.addEventListener('change', () => {
            sampleResponseGroup.classList.toggle('hidden', autoFetchCheckbox.checked);
        });
    }

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        hideError();
        hideResults();
        setLoading(true);

        const endpoint = document.getElementById('endpoint').value.trim();
        const method = document.getElementById('method').value;
        const autoFetch = autoFetchCheckbox ? autoFetchCheckbox.checked : false;
        const headersRaw = document.getElementById('headers').value.trim();
        const requestBodyRaw = document.getElementById('requestBody').value.trim();
        const sampleRaw = document.getElementById('sampleResponse').value.trim();

        // Inline field validation
        setFieldError('endpoint', 'endpointError', null);
        setFieldError('headers', 'headersError', null);
        setFieldError('requestBody', 'requestBodyError', null);
        let valid = true;

        if (!endpoint) {
            setFieldError('endpoint', 'endpointError', 'Please enter a valid URL.');
            valid = false;
        } else {
            try {
                new URL(endpoint);
            } catch (err) {
                setFieldError('endpoint', 'endpointError', 'Please enter a valid URL (e.g., https://api.example.com/items/1).');
                valid = false;
            }
        }

        let headers = null;
        if (headersRaw) {
            try {
                headers = JSON.parse(headersRaw);
                if (headers !== null && typeof headers !== 'object' || Array.isArray(headers)) {
                    setFieldError('headers', 'headersError', 'Headers must be a JSON object (e.g., {"Authorization": "Bearer token"}), not an array or primitive.');
                    valid = false;
                }
            } catch (err) {
                setFieldError('headers', 'headersError', `Invalid JSON format — ${err.message}.`);
                valid = false;
            }
        }

        let requestBody = null;
        if (requestBodyRaw) {
            try {
                requestBody = JSON.parse(requestBodyRaw);
                if (requestBody !== null && typeof requestBody !== 'object' || Array.isArray(requestBody)) {
                    setFieldError('requestBody', 'requestBodyError', 'Request body must be a JSON object (e.g., {"key": "value"}), not an array or primitive.');
                    valid = false;
                }
            } catch (err) {
                setFieldError('requestBody', 'requestBodyError', `Invalid JSON format — ${err.message}.`);
                valid = false;
            }
        }

        if (!valid) {
            setLoading(false);
            return;
        }

        let sampleResponse = null;
        if (sampleRaw && !autoFetch) {
            let jsonText = sampleRaw;

            // Auto-strip HTTP headers if user pasted a raw HTTP response
            const headerEnd = jsonText.indexOf('\r\n\r\n');
            const headerEndAlt = jsonText.indexOf('\n\n');
            if (jsonText.startsWith('HTTP/') && headerEnd !== -1) {
                jsonText = jsonText.slice(headerEnd + 4);
            } else if (jsonText.startsWith('HTTP/') && headerEndAlt !== -1) {
                jsonText = jsonText.slice(headerEndAlt + 2);
            }

            jsonText = jsonText.trim();

            try {
                sampleResponse = JSON.parse(jsonText);
                if (sampleResponse !== null && typeof sampleResponse !== 'object') {
                    showError('Sample response must be valid JSON (object or array), not a primitive value.');
                    setLoading(false);
                    return;
                }
            } catch (err) {
                showError('Sample response is not valid JSON. Please paste only the JSON body, not HTTP headers.');
                setLoading(false);
                return;
            }
        }

        try {
            const res = await fetch('/generate-tests', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    endpoint,
                    method,
                    auto_fetch: autoFetch,
                    headers,
                    request_body: requestBody,
                    sample_response: sampleResponse,
                }),
            });

            const data = await res.json();

            if (!res.ok) {
                const msg = data.detail || data.message || `Server error (${res.status})`;
                showError(msg);
                setLoading(false);
                return;
            }

            lastResult = data;
            lastSessionId = data._session_id || null;
            renderResults(data);
            showResults();
            const totalCases = (data.positive_test_cases || []).length +
                (data.negative_test_cases || []).length +
                (data.edge_cases || []).length +
                (data.assertions || []).length;
            showToast(`✅ ${totalCases} test cases generated`, 'success');
            loadHistory();
        } catch (err) {
            showError('Network error. Please make sure the server is running.');
        } finally {
            setLoading(false);
        }
    });

    copyBtn.addEventListener('click', () => {
        if (!lastResult) return;
        // Strip internal underscore-prefixed keys (_session_id, _provider, _error,
        // nested _dbId) at every depth so the copied JSON is clean, portable output.
        const stripInternal = (key, value) => (key.startsWith('_') ? undefined : value);
        const json = JSON.stringify(lastResult, stripInternal, 2);
        navigator.clipboard.writeText(json).then(() => {
            const original = copyBtn.textContent;
            copyBtn.textContent = 'Copied!';
            copyBtn.classList.add('btn-flash');
            setTimeout(() => {
                copyBtn.textContent = original;
                copyBtn.classList.remove('btn-flash');
            }, 1500);
            showToast('📋 Copied to clipboard', 'success');
        });
    });

    exportBtn.addEventListener('click', () => {
        if (!lastResult) {
            showError('Please generate test cases first before exporting.');
            return;
        }
        downloadFromApi('/export/python', {
            endpoint: document.getElementById('endpoint').value.trim(),
            method: document.getElementById('method').value,
            test_data: lastResult,
        }, 'test_api.py', 'Pytest script exported.');
    });

    const exportPostmanBtn = document.getElementById('exportPostmanBtn');
    exportPostmanBtn.addEventListener('click', () => {
        if (!lastResult) {
            showError('Please generate test cases first before exporting.');
            return;
        }
        downloadFromApi('/export/postman', {
            endpoint: document.getElementById('endpoint').value.trim(),
            method: document.getElementById('method').value,
            test_data: lastResult,
        }, 'collection.json', 'Postman collection exported.');
    });

    const cicdPreview = document.getElementById('cicdPreview');
    const cicdYaml = document.getElementById('cicdYaml');
    const cicdFilename = document.getElementById('cicdFilename');
    const cicdCopyBtn = document.getElementById('cicdCopyBtn');
    const cicdDownloadBtn = document.getElementById('cicdDownloadBtn');
    const cicdCards = document.querySelectorAll('.export-format-card');

    let cicdContent = null;
    let cicdFile = null;

    cicdCards.forEach((card) => {
        card.addEventListener('click', async () => {
            if (!lastResult) {
                showError('Please generate test cases first before exporting.');
                return;
            }

            cicdCards.forEach((c) => c.classList.remove('selected'));
            card.classList.add('selected');

            const endpoint = document.getElementById('endpoint').value.trim();
            const method = document.getElementById('method').value;

            try {
                const res = await fetch('/api/export/cicd', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        format: card.dataset.format,
                        endpoint,
                        method,
                        test_data: lastResult,
                    }),
                });

                const data = await res.json().catch(() => ({}));
                if (!res.ok) {
                    showError(data.detail || `Export failed (${res.status})`);
                    return;
                }

                cicdContent = data.yaml_content;
                cicdFile = data.filename;
                cicdYaml.textContent = cicdContent;
                cicdFilename.textContent = cicdFile;
                cicdPreview.classList.remove('hidden');
            } catch (err) {
                showError('Export failed. Please make sure the server is running.');
            }
        });
    });

    cicdCopyBtn.addEventListener('click', () => {
        if (!cicdContent) return;
        navigator.clipboard.writeText(cicdContent).then(() => {
            const original = cicdCopyBtn.textContent;
            cicdCopyBtn.textContent = 'Copied!';
            cicdCopyBtn.classList.add('btn-flash');
            setTimeout(() => {
                cicdCopyBtn.textContent = original;
                cicdCopyBtn.classList.remove('btn-flash');
            }, 1500);
            showToast('📋 Copied to clipboard', 'success');
        });
    });

    cicdDownloadBtn.addEventListener('click', () => {
        if (!cicdContent) return;
        triggerDownload(new Blob([cicdContent], { type: 'text/yaml' }), cicdFile || 'pipeline.yml');
        showToast('Pipeline config exported.', 'success');
    });

    const runAllBtn = document.getElementById('runAllBtn');
    const executionSummary = document.getElementById('executionSummary');
    const executionProgress = document.getElementById('executionProgress');
    const executionProgressFill = document.getElementById('executionProgressFill');
    const executionProgressText = document.getElementById('executionProgressText');

    function registerCaseCard(el, data, category) {
        const entry = {
            el,
            data,
            category,
            runBtn: el.querySelector('.btn-run'),
            row: el.querySelector('.case-row'),
            body: el.querySelector('.case-body'),
            detailsEl: el.querySelector('.case-details'),
        };
        entry.row.addEventListener('click', () => {
            const nowHidden = entry.body.classList.toggle('hidden');
            entry.el.classList.toggle('open', !nowHidden);
        });
        entry.runBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            runSingleTest(entry);
        });
        caseCards.push(entry);
    }

    function getRequestExtras() {
        let headers = null;
        let body = null;
        try {
            const raw = document.getElementById('headers').value.trim();
            if (raw) headers = JSON.parse(raw);
        } catch (err) { /* send without headers if unparseable */ }
        try {
            const raw = document.getElementById('requestBody').value.trim();
            if (raw) body = JSON.parse(raw);
        } catch (err) { /* send without body if unparseable */ }
        return { headers, body };
    }

    function buildDetailsHtml(result) {
        let html = `<div class="detail-row"><strong>Status:</strong> expected ${result.expected_status ?? '—'} vs actual ${result.actual_status}</div>`;
        if (result.error_message) {
            html += `<div class="detail-row assertion-fail">${escapeHtml(result.error_message)}</div>`;
        }
        if (Array.isArray(result.assertion_results) && result.assertion_results.length) {
            html += '<ul class="assertion-list">' + result.assertion_results.map((a) =>
                `<li class="${a.passed ? 'assertion-pass' : 'assertion-fail'}">` +
                `${a.passed ? '&#10003;' : '&#10007;'} ${escapeHtml(a.assertion)} — ${escapeHtml(a.detail)}</li>`
            ).join('') + '</ul>';
        }
        if (result.actual_response_preview) {
            html += `<pre class="response-preview">${escapeHtml(result.actual_response_preview)}</pre>`;
        }
        return html;
    }

    function applyExecutionResult(entry, result) {
        entry.el.classList.remove('test-pass', 'test-fail');
        entry.el.classList.add(result.passed ? 'test-pass' : 'test-fail');

        // Status badge in the compact row
        const badge = entry.el.querySelector('.status-badge');
        badge.className = `status-badge ${result.passed ? 'passed' : 'failed'}`;
        badge.textContent = result.passed ? 'PASSED' : 'FAILED';

        entry.detailsEl.innerHTML = buildDetailsHtml(result);
        entry.detailsEl.classList.remove('hidden');
        // Reveal the card body so the outcome is visible; auto-expand on failure.
        if (!result.passed) {
            entry.body.classList.remove('hidden');
            entry.el.classList.add('open');
        }
        entry.runBtn.textContent = 'Re-run';
    }

    async function executeOne(entry) {
        const { headers, body } = getRequestExtras();
        const res = await fetch('/api/execute-single', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                endpoint: document.getElementById('endpoint').value.trim(),
                method: document.getElementById('method').value,
                headers,
                body,
                test_case: { ...entry.data, category: entry.category },
                session_id: lastSessionId,
            }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
            throw new Error(data.detail || `Execution failed (${res.status})`);
        }
        return data;
    }

    async function runSingleTest(entry) {
        hideError();
        entry.runBtn.disabled = true;
        entry.runBtn.textContent = 'Running...';
        try {
            const result = await executeOne(entry);
            applyExecutionResult(entry, result);
            validateContract(result);
        } catch (err) {
            showError(err.message || 'Execution failed. Is the server running?');
            entry.runBtn.textContent = 'Run';
        } finally {
            entry.runBtn.disabled = false;
        }
    }

    function setExecutionUiDisabled(disabled) {
        runAllBtn.disabled = disabled;
        caseCards.forEach((entry) => { entry.runBtn.disabled = disabled; });
    }

    runAllBtn.addEventListener('click', async () => {
        if (!lastResult || !caseCards.length) {
            showError('Please generate test cases first before running them.');
            return;
        }
        hideError();
        executionSummary.classList.add('hidden');
        executionProgress.classList.remove('hidden');
        setExecutionUiDisabled(true);

        const total = caseCards.length;
        let passed = 0;
        let failed = 0;
        let totalDuration = 0;
        let lastExecution = null;

        for (let i = 0; i < total; i++) {
            const entry = caseCards[i];
            executionProgressText.textContent = `Running test ${i + 1} of ${total}...`;
            executionProgressFill.style.width = `${(i / total) * 100}%`;
            try {
                const result = await executeOne(entry);
                applyExecutionResult(entry, result);
                lastExecution = result;
                totalDuration += result.duration_ms || 0;
                if (result.passed) { passed++; } else { failed++; }
            } catch (err) {
                failed++;
                applyExecutionResult(entry, {
                    passed: false,
                    expected_status: null,
                    actual_status: 0,
                    assertion_results: [],
                    actual_response_preview: '',
                    error_message: err.message || 'Execution failed',
                });
            }
        }

        executionProgressFill.style.width = '100%';
        executionProgressText.textContent = 'Done';
        if (lastExecution) validateContract(lastExecution);
        const rate = total ? Math.round((passed / total) * 100) : 0;
        executionSummary.innerHTML =
            `<span>✅ ${passed} Passed</span>` +
            `<span>❌ ${failed} Failed</span>` +
            `<span>📊 ${rate}%</span>` +
            `<span>⏱️ ${totalDuration}ms</span>` +
            `<button type="button" class="summary-dismiss" aria-label="Dismiss">&times;</button>`;
        executionSummary.querySelector('.summary-dismiss').addEventListener('click', () => {
            executionSummary.classList.add('hidden');
        });
        executionSummary.classList.remove('hidden');
        setExecutionUiDisabled(false);
        showToast(
            `Execution complete: ${passed}/${total} passed.`,
            failed ? 'error' : 'success'
        );
        loadHistory();
    });

    // --- Security scanning mode ---
    const modeFunctionalBtn = document.getElementById('modeFunctional');
    const modeSecurityBtn = document.getElementById('modeSecurity');
    const modeSubtitle = document.getElementById('modeSubtitle');
    const securitySection = document.getElementById('securitySection');
    const securityResults = document.getElementById('securityResults');
    const securityWarning = document.getElementById('securityWarning');
    const dismissWarningBtn = document.getElementById('dismissWarningBtn');
    const owaspCategoriesEl = document.getElementById('owaspCategories');
    let activeSeverity = 'all';
    const runScanBtn = document.getElementById('runScanBtn');
    const riskScoreFill = document.getElementById('riskScoreFill');
    const riskScoreLabel = document.getElementById('riskScoreLabel');
    const scanSummary = document.getElementById('scanSummary');
    const findingsBody = document.getElementById('findingsBody');
    const exportSecMdBtn = document.getElementById('exportSecMdBtn');
    const exportSecHtmlBtn = document.getElementById('exportSecHtmlBtn');

    let lastScan = null;
    let owaspLoaded = false;

    function setMode(mode) {
        const isSecurity = mode === 'security';
        modeFunctionalBtn.classList.toggle('active', !isSecurity);
        modeSecurityBtn.classList.toggle('active', isSecurity);
        document.body.classList.toggle('security-mode-active', isSecurity);
        modeSubtitle.textContent = isSecurity
            ? 'Scan for OWASP API Top 10 vulnerabilities'
            : 'Generate and execute API test cases';
        securitySection.classList.toggle('hidden', !isSecurity);
        submitBtn.classList.toggle('hidden', isSecurity);
        hideError();
        hideResults();
        // Switching modes always resets the Results panel to its empty state.
        securityResults.classList.add('hidden');
        if (isSecurity && !owaspLoaded) {
            loadOwaspCategories();
        }
    }

    // --- Sidebar view router (Modes + Workspace) ---
    const viewTitle = document.getElementById('viewTitle');
    const mainHeaderActions = document.querySelector('.main-header-actions');
    const viewEls = {
        testing: document.getElementById('view-testing'),
        history: document.getElementById('view-history'),
        collections: document.getElementById('view-collections'),
        documentation: document.getElementById('view-documentation'),
    };
    const VIEW_META = {
        history: { title: 'History', subtitle: 'Reload, rerun, or delete past sessions' },
        collections: { title: 'Collections', subtitle: 'Saved request collections' },
        documentation: { title: 'Documentation', subtitle: 'How to use API Sentinel' },
    };

    function setActiveNav(id) {
        document.querySelectorAll('.nav-item').forEach((n) => n.classList.remove('active'));
        const el = document.getElementById(id);
        if (el) el.classList.add('active');
    }

    function showView(name) {
        Object.entries(viewEls).forEach(([key, el]) => {
            if (el) el.classList.toggle('hidden', key !== name);
        });
        if (mainHeaderActions) mainHeaderActions.classList.toggle('hidden', name !== 'testing');
        const meta = VIEW_META[name];
        if (meta) {
            viewTitle.textContent = meta.title;
            modeSubtitle.textContent = meta.subtitle;
        }
        if (name === 'history') loadHistory();
        setSidebarOpen(false);
    }

    function goToTesting(mode) {
        setMode(mode);
        viewTitle.textContent = mode === 'security' ? 'Security Scanning' : 'Functional Testing';
        setActiveNav(mode === 'security' ? 'modeSecurity' : 'modeFunctional');
        showView('testing');
    }

    modeFunctionalBtn.addEventListener('click', () => goToTesting('functional'));
    modeSecurityBtn.addEventListener('click', () => goToTesting('security'));
    document.getElementById('navHistory').addEventListener('click', () => {
        setActiveNav('navHistory');
        showView('history');
    });
    document.getElementById('navCollections').addEventListener('click', () => {
        setActiveNav('navCollections');
        showView('collections');
    });
    document.getElementById('navDocs').addEventListener('click', () => {
        setActiveNav('navDocs');
        showView('documentation');
    });

    dismissWarningBtn.addEventListener('click', () => securityWarning.classList.add('hidden'));

    async function loadOwaspCategories() {
        try {
            const res = await fetch('/api/security/owasp-categories');
            const categories = await res.json();
            owaspCategoriesEl.innerHTML = categories.map((c) => `
                <label class="owasp-category-item">
                    <input type="checkbox" value="${escapeHtml(c.id)}" checked>
                    <span>
                        <strong>${escapeHtml(c.id)}</strong> — ${escapeHtml(c.name)}<br>
                        <span class="cat-desc">${escapeHtml(c.description)}</span>
                    </span>
                </label>
            `).join('');
            owaspLoaded = true;
        } catch (err) {
            owaspCategoriesEl.innerHTML = '<p class="case-desc">Could not load OWASP categories. Is the server running?</p>';
        }
    }

    function selectedCategories() {
        return Array.from(owaspCategoriesEl.querySelectorAll('input:checked')).map((cb) => cb.value);
    }

    function riskBand(score) {
        if (score >= 76) return { cls: 'risk-red', label: 'Critical Risk' };
        if (score >= 51) return { cls: 'risk-orange', label: 'High Risk' };
        if (score >= 26) return { cls: 'risk-yellow', label: 'Medium Risk' };
        return { cls: 'risk-green', label: 'Low Risk' };
    }

    function renderScanResults(data) {
        const band = riskBand(data.risk_score || 0);
        riskScoreFill.style.width = `${data.risk_score || 0}%`;
        riskScoreFill.className = `risk-score-fill ${band.cls}`;
        riskScoreLabel.textContent = `Risk Score: ${data.risk_score}/100 — ${band.label}`;

        const s = data.summary || {};
        scanSummary.innerHTML =
            `<span class="chip">${s.total_tests || 0} tests</span>` +
            `<span class="chip status-vulnerable">${s.vulnerable_count || 0} vulnerable</span>` +
            `<span class="chip status-review">${s.needs_review_count || 0} needs review</span>` +
            `<span class="chip">Critical: ${s.critical || 0}</span>` +
            `<span class="chip">High: ${s.high || 0}</span>` +
            `<span class="chip">Medium: ${s.medium || 0}</span>` +
            `<span class="chip">Low: ${s.low || 0}</span>` +
            `<span class="chip">${((data.scan_duration_ms || 0) / 1000).toFixed(1)}s</span>`;

        activeSeverity = 'all';
        updateSeverityTabCounts();
        document.querySelectorAll('#severityTabs .result-tab').forEach((t) => {
            t.classList.toggle('active', t.dataset.sev === 'all');
        });
        renderFindings();
        // Take over the Results panel: hide the functional empty/skeleton/results.
        emptyState.classList.add('hidden');
        resultsSkeleton.classList.add('hidden');
        resultsSection.classList.add('hidden');
        securityResults.classList.remove('hidden');
    }

    function expectedForFinding(f) {
        const id = f.test_case_id || '';
        if (id.startsWith('SEC-API6-01')) return '429';
        if (id.startsWith('SEC-API5-02')) return '405';
        if (id.startsWith('SEC-API7') || id.startsWith('SEC-API10')) return '400';
        if (id.startsWith('SEC-API8-01')) return 'clean 400';
        if (id.startsWith('SEC-API8-02')) return 'headers set';
        return '401/403';
    }

    function renderFindings() {
        if (!lastScan) return;
        const filter = activeSeverity;
        const findings = (lastScan.findings || []).filter(
            (f) => filter === 'all' || f.severity === filter
        );

        findingsBody.innerHTML = '';
        findings.forEach((f) => {
            const verdict = f.finding || '';
            const statusCls =
                verdict === 'Vulnerable' ? 'status-vulnerable' :
                verdict === 'Secure' ? 'status-secure' :
                verdict === 'Needs Review' ? 'status-review' : 'status-error';
            const rowCls =
                verdict === 'Vulnerable' ? 'finding-vulnerable' :
                verdict === 'Needs Review' ? 'finding-review' : 'finding-secure';
            const owaspShort = (f.owasp_category || '').split(' - ')[0];

            const tr = document.createElement('tr');
            tr.className = rowCls;
            tr.innerHTML = `
                <td><span class="severity-badge severity-${(f.severity || '').toLowerCase()}">${escapeHtml(f.severity || '')}</span></td>
                <td>${escapeHtml(owaspShort)}</td>
                <td>${escapeHtml(f.title || '')}</td>
                <td><span class="${statusCls}">Expected <code>${expectedForFinding(f)}</code>, Got <code>${f.actual_status}</code> &rarr; ${escapeHtml(f.finding || '')}</span></td>
                <td><button type="button" class="expand-btn">Details <span class="chevron">▾</span></button></td>
            `;

            const detailTr = document.createElement('tr');
            detailTr.className = 'finding-detail-row hidden';
            const detailTd = document.createElement('td');
            detailTd.colSpan = 5;
            let detailHtml = `<strong>OWASP:</strong> ${escapeHtml(f.owasp_category || '')}<br>`;
            if (f.finding_reason) {
                detailHtml += `<strong>Reason:</strong> ${escapeHtml(f.finding_reason)}<br>`;
            }
            detailHtml += `<strong>Payload used:</strong><pre class="payload-display">${escapeHtml(JSON.stringify(f.payload_used || {}, null, 2))}</pre>`;
            if (f.error_message) {
                detailHtml += `<div class="assertion-fail">${escapeHtml(f.error_message)}</div>`;
            }
            if (f.actual_response_preview) {
                detailHtml += `<strong>Response preview:</strong><pre class="response-preview">${escapeHtml(f.actual_response_preview)}</pre>`;
            }
            detailHtml += `<strong>Remediation:</strong> ${escapeHtml(f.remediation || '')}`;
            detailTd.innerHTML = detailHtml;
            detailTr.appendChild(detailTd);

            tr.querySelector('.expand-btn').addEventListener('click', (e) => {
                detailTr.classList.toggle('hidden');
                e.currentTarget.classList.toggle('expanded');
            });
            findingsBody.appendChild(tr);
            findingsBody.appendChild(detailTr);
        });
    }

    function updateSeverityTabCounts() {
        const findings = (lastScan && lastScan.findings) || [];
        const by = { Critical: 0, High: 0, Medium: 0, Low: 0 };
        findings.forEach((f) => { if (by[f.severity] !== undefined) by[f.severity]++; });
        const set = (id, n) => { const el = document.getElementById(id); if (el) el.textContent = n; };
        set('sevcAll', findings.length);
        set('sevcCritical', by.Critical);
        set('sevcHigh', by.High);
        set('sevcMedium', by.Medium);
        set('sevcLow', by.Low);
    }

    function setSeverity(sev) {
        activeSeverity = sev;
        document.querySelectorAll('#severityTabs .result-tab').forEach((t) => {
            t.classList.toggle('active', t.dataset.sev === sev);
        });
        renderFindings();
    }

    document.querySelectorAll('#severityTabs .result-tab').forEach((tab) => {
        tab.addEventListener('click', () => setSeverity(tab.dataset.sev));
    });

    runScanBtn.addEventListener('click', async () => {
        hideError();
        const endpoint = document.getElementById('endpoint').value.trim();
        if (!endpoint) {
            showError('Please enter an API endpoint to scan.');
            return;
        }
        const categories = selectedCategories();
        if (!categories.length) {
            showError('Select at least one OWASP category to scan.');
            return;
        }

        const { headers, body } = getRequestExtras();
        let sampleResponse = null;
        const sampleRaw = document.getElementById('sampleResponse').value.trim();
        if (sampleRaw) {
            try { sampleResponse = JSON.parse(sampleRaw); } catch (err) { /* optional field */ }
        }

        runScanBtn.disabled = true;
        runScanBtn.querySelector('.btn-text').textContent = 'Scanning...';
        runScanBtn.querySelector('.spinner').classList.remove('hidden');

        try {
            const res = await fetch('/api/security/scan', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    endpoint,
                    method: document.getElementById('method').value,
                    headers,
                    body,
                    sample_response: sampleResponse,
                    categories,
                }),
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) {
                showError(data.detail || `Scan failed (${res.status})`);
                return;
            }
            lastScan = data;
            renderScanResults(data);
            showToast(
                `✅ Security scan complete — ${(data.summary || {}).vulnerable_count || 0} vulnerabilities found`,
                'success'
            );
        } catch (err) {
            showError('Scan failed. Please make sure the server is running.');
        } finally {
            runScanBtn.disabled = false;
            runScanBtn.querySelector('.btn-text').textContent = 'Run Security Scan';
            runScanBtn.querySelector('.spinner').classList.add('hidden');
        }
    });

    function exportSecurityReport(format) {
        if (!lastScan) {
            showError('Run a security scan first.');
            return;
        }
        downloadFromApi('/api/security/report', {
            format,
            endpoint: document.getElementById('endpoint').value.trim(),
            scan: lastScan,
        }, format === 'markdown' ? 'security-report.md' : 'security-report.html', 'Security report exported.');
    }

    exportSecMdBtn.addEventListener('click', () => exportSecurityReport('markdown'));
    exportSecHtmlBtn.addEventListener('click', () => exportSecurityReport('html'));

    // --- OpenAPI import & contract testing ---
    const importOpenapiBtn = document.getElementById('importOpenapiBtn');
    const openapiModal = document.getElementById('openapiModal');
    const closeOpenapiModalBtn = document.getElementById('closeOpenapiModal');
    const openapiSpecText = document.getElementById('openapiSpecText');
    const parseSpecBtn = document.getElementById('parseSpecBtn');
    const openapiError = document.getElementById('openapiError');
    const openapiInfo = document.getElementById('openapiInfo');
    const endpointExplorer = document.getElementById('endpointExplorer');
    const contractPanel = document.getElementById('contractPanel');
    const contractResults = document.getElementById('contractResults');

    let parsedSpec = null;
    let selectedContractEndpoint = null;

    importOpenapiBtn.addEventListener('click', () => openapiModal.classList.remove('hidden'));
    closeOpenapiModalBtn.addEventListener('click', () => openapiModal.classList.add('hidden'));
    openapiModal.addEventListener('click', (e) => {
        if (e.target === openapiModal) openapiModal.classList.add('hidden');
    });

    function showOpenapiError(msg) {
        openapiError.textContent = msg;
        openapiError.classList.remove('hidden');
        openapiInfo.classList.add('hidden');
        endpointExplorer.classList.add('hidden');
    }

    function _sampleFromSchema(schema) {
        if (!schema || typeof schema !== 'object') return null;
        if (schema.example !== undefined) return schema.example;
        const type = Array.isArray(schema.type) ? schema.type[0] : schema.type;
        if (type === 'object' || schema.properties) {
            const obj = {};
            const props = schema.properties || {};
            for (const key of Object.keys(props)) {
                obj[key] = _sampleFromSchema(props[key]);
            }
            return obj;
        }
        if (type === 'array') return [_sampleFromSchema(schema.items || {})];
        if (schema.enum && schema.enum.length) return schema.enum[0];
        if (type === 'integer' || type === 'number') return 1;
        if (type === 'boolean') return true;
        if (type === 'string') return 'string';
        return null;
    }

    function renderEndpointExplorer(parsed) {
        endpointExplorer.innerHTML = '';
        parsed.endpoints.forEach((ep) => {
            const item = document.createElement('div');
            item.className = 'endpoint-item';
            const methodCls = `method-badge-${ep.method.toLowerCase()}`;
            item.innerHTML = `
                <span class="method-badge ${methodCls}">${escapeHtml(ep.method)}</span>
                <span class="endpoint-path">${escapeHtml(ep.path)}</span>
                <span class="endpoint-summary">${escapeHtml(ep.summary || '')}</span>
            `;
            item.addEventListener('click', () => selectSpecEndpoint(ep));
            endpointExplorer.appendChild(item);
        });
        endpointExplorer.classList.remove('hidden');
    }

    function selectSpecEndpoint(ep) {
        const base = (parsedSpec.base_url || '').replace(/\/+$/, '');
        document.getElementById('endpoint').value = base ? base + ep.path : ep.path;
        document.getElementById('method').value = ep.method;
        if (ep.request_body_schema) {
            const sample = _sampleFromSchema(ep.request_body_schema);
            if (sample) {
                document.getElementById('requestBody').value = JSON.stringify(sample, null, 2);
            }
        }
        selectedContractEndpoint = ep;
        openapiModal.classList.add('hidden');
    }

    parseSpecBtn.addEventListener('click', async () => {
        openapiError.classList.add('hidden');
        const spec = openapiSpecText.value.trim();
        if (!spec) {
            showOpenapiError('Please paste an OpenAPI spec first.');
            return;
        }
        parseSpecBtn.disabled = true;
        try {
            const res = await fetch('/api/openapi/parse', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ spec }),
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) {
                showOpenapiError(data.detail || `Parse failed (${res.status})`);
                return;
            }
            parsedSpec = data;
            openapiInfo.textContent =
                `${data.info.title || 'Untitled API'} (spec ${data.info.version || '?'}) — ` +
                `${data.endpoints.length} endpoints${data.base_url ? ` · base: ${data.base_url}` : ''}. Click an endpoint to load it.`;
            openapiInfo.classList.remove('hidden');
            renderEndpointExplorer(data);
        } catch (err) {
            showOpenapiError('Parse failed. Please make sure the server is running.');
        } finally {
            parseSpecBtn.disabled = false;
        }
    });

    async function validateContract(result) {
        if (!selectedContractEndpoint || !result || !result.actual_response_preview) return;
        const schemas = selectedContractEndpoint.response_schemas || {};
        const schema = schemas[String(result.actual_status)] || schemas['default'];
        if (!schema) {
            contractPanel.classList.remove('hidden');
            contractResults.innerHTML =
                `<div class="case-desc">HTTP ${result.actual_status} is not documented in the spec — no schema to validate against.</div>`;
            return;
        }
        let actual;
        try {
            actual = JSON.parse(result.actual_response_preview);
        } catch (err) {
            contractPanel.classList.remove('hidden');
            contractResults.innerHTML =
                '<div class="case-desc">Response is not valid JSON (or preview was truncated) — contract validation skipped.</div>';
            return;
        }
        try {
            const res = await fetch('/api/openapi/validate-response', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ actual_response: actual, schema }),
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) return;
            contractPanel.classList.remove('hidden');
            if (data.valid) {
                contractResults.innerHTML =
                    '<div class="contract-valid">&#10003; Contract valid — response matches the documented schema.</div>';
            } else {
                contractResults.innerHTML =
                    `<div class="case-desc">${data.violation_count} schema violation(s):</div>` +
                    data.violations.map((v) =>
                        `<div class="schema-violation"><span class="violation-path">${escapeHtml(v.path)}</span> — ${escapeHtml(v.message)}</div>`
                    ).join('');
            }
        } catch (err) { /* validation is best-effort */ }
    }

    // --- Inline field validation helper ---
    function setFieldError(inputId, errorId, msg) {
        const input = document.getElementById(inputId);
        const errEl = document.getElementById(errorId);
        if (msg) {
            input.classList.add('input-invalid');
            errEl.textContent = msg;
            errEl.classList.remove('hidden');
        } else {
            input.classList.remove('input-invalid');
            errEl.classList.add('hidden');
        }
    }

    // --- Toasts ---
    const toastContainer = document.getElementById('toastContainer');
    const TOAST_ICONS = { success: '✅', error: '❌', info: 'ℹ️' };

    function showToast(message, type = 'info') {
        const toast = document.createElement('div');
        toast.className = `toast toast-${type}`;
        const icon = document.createElement('span');
        icon.className = 'toast-icon';
        icon.textContent = TOAST_ICONS[type] || TOAST_ICONS.info;
        const text = document.createElement('span');
        text.textContent = message;
        toast.appendChild(icon);
        toast.appendChild(text);
        toastContainer.appendChild(toast);
        setTimeout(() => {
            toast.classList.add('toast-leaving');
            setTimeout(() => toast.remove(), 250);
        }, 4000);
    }

    // --- History sidebar ---
    const historyList = document.getElementById('historyList');
    const menuToggle = document.getElementById('menuToggle');
    const sidebarBackdrop = document.getElementById('sidebarBackdrop');
    const appSidebar = document.getElementById('appSidebar');

    function setSidebarOpen(open) {
        if (!appSidebar || !sidebarBackdrop) return;
        appSidebar.classList.toggle('open', open);
        sidebarBackdrop.classList.toggle('open', open);
    }
    if (menuToggle) {
        menuToggle.addEventListener('click', () => {
            setSidebarOpen(!(appSidebar && appSidebar.classList.contains('open')));
        });
    }
    if (sidebarBackdrop) {
        sidebarBackdrop.addEventListener('click', () => setSidebarOpen(false));
    }

    let allSessions = [];
    let historyMode = 'functional';

    async function loadHistory() {
        try {
            const res = await fetch('/api/history/sessions');
            const data = await res.json();
            allSessions = data.sessions || [];
        } catch (err) {
            allSessions = [];
            historyList.innerHTML = '<p class="case-desc">History unavailable.</p>';
            return;
        }
        renderHistory();
    }

    function isSecuritySession(s) {
        return s.mode === 'security';
    }

    function renderHistory() {
        const funcCount = allSessions.filter((s) => !isSecuritySession(s)).length;
        const secCount = allSessions.filter(isSecuritySession).length;
        const fc = document.getElementById('histCountFunctional');
        const sc = document.getElementById('histCountSecurity');
        if (fc) fc.textContent = funcCount;
        if (sc) sc.textContent = secCount;

        const sessions = allSessions.filter((s) =>
            historyMode === 'security' ? isSecuritySession(s) : !isSecuritySession(s)
        );

        if (!sessions.length) {
            const hint = historyMode === 'security'
                ? 'Run a security scan to see it here'
                : 'Generate tests to see them here';
            historyList.innerHTML = `
                <div class="history-empty">
                    <p>No ${historyMode} sessions yet</p>
                    <p class="history-empty-hint">${hint}</p>
                </div>
            `;
            return;
        }
        historyList.innerHTML = '';
        sessions.forEach((s) => {
            const security = isSecuritySession(s);
            const item = document.createElement('div');
            item.className = 'history-item';
            const date = (s.created_at || '').slice(0, 16).replace('T', ' ');
            const execInfo = s.executed_count ? ` · ${s.passed_count}/${s.executed_count} passed` : '';
            // Rerun re-executes stored functional tests — not meaningful for scans.
            const rerunBtn = security
                ? ''
                : '<button type="button" data-action="rerun" data-tooltip="Re-runs all tests from this session">Rerun</button>';
            item.innerHTML = `
                <span class="history-endpoint">${escapeHtml(s.method)} ${escapeHtml(s.endpoint)}</span>
                <div class="history-meta">
                    <span>${escapeHtml(date)}</span>
                    <span>${s.test_count || 0} ${security ? 'checks' : 'tests'}${execInfo}</span>
                </div>
                <div class="history-actions">
                    <button type="button" data-action="load">Load</button>
                    ${rerunBtn}
                    <button type="button" data-action="delete">Delete</button>
                </div>
            `;
            item.querySelector('[data-action="load"]').addEventListener('click', () => {
                historyList.querySelectorAll('.history-item.selected').forEach((el) => el.classList.remove('selected'));
                item.classList.add('selected');
                loadSession(s.id, s.mode);
            });
            const rerunEl = item.querySelector('[data-action="rerun"]');
            if (rerunEl) rerunEl.addEventListener('click', () => rerunSession(s.id));
            item.querySelector('[data-action="delete"]').addEventListener('click', () => deleteSession(s.id));
            historyList.appendChild(item);
        });
    }

    document.querySelectorAll('#historyTabs .history-tab').forEach((tab) => {
        tab.addEventListener('click', () => {
            historyMode = tab.dataset.mode;
            document.querySelectorAll('#historyTabs .history-tab').forEach((t) => t.classList.remove('active'));
            tab.classList.add('active');
            renderHistory();
        });
    });

    // Rebuild the generation-shaped result object from stored (flattened) test
    // case rows so the results panel can be re-rendered without another LLM call.
    function reshapeStoredCases(testCases) {
        const result = {
            positive_test_cases: [],
            negative_test_cases: [],
            edge_cases: [],
            assertions: [],
        };
        const catKey = {
            positive: 'positive_test_cases',
            negative: 'negative_test_cases',
            edge: 'edge_cases',
        };
        (testCases || []).forEach((tc) => {
            if (tc.category === 'assertion') {
                // Reverse of generate.py _cases_for_db: title=rule, description=category.
                // ids are carried (not shown for assertions) so stored execution
                // results can be matched back on restore. Assertions have no LLM
                // id, so the DB row id is the only key available.
                result.assertions.push({
                    id: String(tc.id),
                    _dbId: String(tc.id),
                    category: tc.description || 'general',
                    rule: tc.title || '',
                    severity: tc.severity || 'medium',
                });
                return;
            }
            const key = catKey[tc.category] || 'positive_test_cases';
            const expected = {};
            if (tc.expected_status !== null && tc.expected_status !== undefined) {
                expected.status_code = tc.expected_status;
            }
            if (Array.isArray(tc.assertions) && tc.assertions.length) {
                expected.validation_rules = tc.assertions;
            }
            result[key].push({
                // Prefer the original LLM id (e.g. "TC-POS-01") so this matches the
                // fresh-generate shape, live-run results, and future reruns. _dbId
                // is kept as a fallback to match legacy reruns keyed by DB row id.
                id: String(tc.case_ref || tc.id),
                _dbId: String(tc.id),
                title: tc.title || 'Untitled',
                description: tc.description || '',
                expected,
                request: tc.payload || undefined,
            });
        });
        return result;
    }

    // Re-apply stored PASS/FAIL results to restored cards. Best-effort: only
    // results keyed by the DB row id (i.e. history reruns) match; unmatched
    // cards simply stay "Pending" rather than showing anything misleading.
    function applyStoredExecutionResults(execResults) {
        if (!Array.isArray(execResults) || !execResults.length) return;
        const byId = {};
        execResults.forEach((r) => { byId[String(r.test_case_id)] = r; });
        caseCards.forEach((entry) => {
            // Match by the original LLM id first (live runs / new reruns), then
            // fall back to the DB row id (legacy reruns).
            const r = byId[String(entry.data.id)] ||
                (entry.data._dbId ? byId[String(entry.data._dbId)] : null);
            if (!r) return;
            applyExecutionResult(entry, {
                passed: !!r.passed,
                expected_status: entry.data.expected ? entry.data.expected.status_code ?? null : null,
                actual_status: r.actual_status,
                assertion_results: [],
                actual_response_preview: r.actual_response || '',
                error_message: r.error_message || null,
            });
        });
    }

    async function loadSession(id, mode) {
        try {
            const res = await fetch(`/api/history/sessions/${id}`);
            const s = await res.json().catch(() => ({}));
            if (!res.ok) {
                showToast(s.detail || 'Could not load session.', 'error');
                return;
            }
            document.getElementById('endpoint').value = s.endpoint || '';
            document.getElementById('method').value = s.method || 'GET';
            const headersJson = s.headers ? JSON.stringify(s.headers, null, 2) : '';
            const bodyJson = s.body ? JSON.stringify(s.body, null, 2) : '';
            const sampleJson = s.sample_response ? JSON.stringify(s.sample_response, null, 2) : '';
            document.getElementById('headers').value = headersJson;
            document.getElementById('requestBody').value = bodyJson;
            document.getElementById('sampleResponse').value = sampleJson;
            // Credentials (in the URL, headers, body, or sample response) are redacted
            // before storage — warn the user they must be re-entered before running
            // authenticated requests against this session.
            if (((s.endpoint || '') + headersJson + bodyJson + sampleJson).includes('***REDACTED***')) {
                document.getElementById('advancedFields').open = true;
                showToast('Stored credentials were redacted — re-enter them to run authenticated requests.', 'info');
            }

            const isSecurity = mode === 'security';
            goToTesting(isSecurity ? 'security' : 'functional');

            // Restore the generated test cases (and any run results) so the user
            // sees them instantly instead of having to regenerate. Security scan
            // results aren't reconstructable from stored rows — form-only for now.
            const cases = s.test_cases || [];
            if (!isSecurity && cases.length) {
                const restored = reshapeStoredCases(cases);
                lastResult = restored;
                lastSessionId = id;
                renderResults(restored);
                showResults();
                applyStoredExecutionResults(s.execution_results);
                showToast('Session restored — cases and results loaded.', 'success');
            } else {
                showToast('Session loaded into the form.', 'success');
            }
        } catch (err) {
            showToast('Could not load session.', 'error');
        }
    }

    async function rerunSession(id) {
        try {
            const res = await fetch(`/api/history/sessions/${id}/rerun`, { method: 'POST' });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) {
                showToast(data.detail || 'Rerun failed.', 'error');
                return;
            }
            const sum = data.summary;
            showToast(
                `Rerun complete: ${sum.passed}/${sum.total} passed (${sum.pass_rate})`,
                sum.failed ? 'error' : 'success'
            );
            loadHistory();
        } catch (err) {
            showToast('Rerun failed. Is the API reachable?', 'error');
        }
    }

    async function deleteSession(id) {
        try {
            const res = await fetch(`/api/history/sessions/${id}`, { method: 'DELETE' });
            if (res.ok) {
                showToast('Session deleted.', 'success');
                loadHistory();
            } else {
                showToast('Delete failed.', 'error');
            }
        } catch (err) {
            showToast('Delete failed.', 'error');
        }
    }

    loadHistory();

    // --- Keyboard shortcuts ---
    document.addEventListener('keydown', (e) => {
        if (e.ctrlKey && e.shiftKey && e.key === 'Enter') {
            e.preventDefault();
            runAllBtn.click();
        } else if (e.ctrlKey && e.key === 'Enter') {
            e.preventDefault();
            form.requestSubmit();
        } else if (e.ctrlKey && (e.key === 'd' || e.key === 'D')) {
            e.preventDefault();
            if (window.darkMode) window.darkMode.toggleDarkMode();
        }
    });

    const resultsSkeleton = document.getElementById('resultsSkeleton');
    const emptyState = document.getElementById('emptyState');

    function setLoading(isLoading) {
        submitBtn.disabled = isLoading;
        const autoFetch = autoFetchCheckbox ? autoFetchCheckbox.checked : false;
        if (isLoading) {
            btnText.textContent = autoFetch ? 'Fetching API response...' : 'Generating...';
        } else {
            btnText.textContent = 'Generate Test Cases';
        }
        spinner.classList.toggle('hidden', !isLoading);
        submitBtn.classList.toggle('btn-loading', isLoading);
        resultsSkeleton.classList.toggle('hidden', !isLoading);
        if (isLoading) {
            emptyState.classList.add('hidden');
        } else if (resultsSection.classList.contains('hidden')) {
            emptyState.classList.remove('hidden');
        }
    }

    function showError(msg) {
        errorMessage.textContent = msg;
        errorSection.classList.remove('hidden');
        clearTimeout(errorTimer);
        errorTimer = setTimeout(hideError, 8000);
    }

    function hideError() {
        errorSection.classList.add('hidden');
        clearTimeout(errorTimer);
    }

    let errorTimer = null;
    const errorDismiss = document.getElementById('errorDismiss');
    errorDismiss.addEventListener('click', hideError);
    errorSection.addEventListener('mouseenter', () => clearTimeout(errorTimer));
    errorSection.addEventListener('mouseleave', () => {
        if (!errorSection.classList.contains('hidden')) {
            clearTimeout(errorTimer);
            errorTimer = setTimeout(hideError, 8000);
        }
    });

    function showResults() {
        resultsSection.classList.remove('hidden');
        emptyState.classList.add('hidden');
        securityResults.classList.add('hidden');
    }

    function hideResults() {
        resultsSection.classList.add('hidden');
        emptyState.classList.remove('hidden');
        cicdPreview.classList.add('hidden');
        cicdCards.forEach((c) => c.classList.remove('selected'));
        cicdContent = null;
        cicdFile = null;
        executionSummary.classList.add('hidden');
        executionProgress.classList.add('hidden');
        caseCards = [];
    }

    const PROVIDER_NAMES = { mistral: 'Mistral', groq: 'Groq', github: 'GitHub Models' };
    const providerBadge = document.getElementById('providerBadge');

    function renderProviderBadge(provider) {
        if (!providerBadge) return;
        const name = PROVIDER_NAMES[provider];
        if (name) {
            providerBadge.textContent = `Powered by ${name}`;
            providerBadge.classList.remove('hidden');
        } else {
            providerBadge.classList.add('hidden');
        }
    }

    function renderResults(data) {
        caseCards = [];
        executionSummary.classList.add('hidden');
        executionProgress.classList.add('hidden');
        contractPanel.classList.add('hidden');
        const positive = data.positive_test_cases || [];
        const negative = data.negative_test_cases || [];
        const edge = data.edge_cases || [];
        const assertions = data.assertions || [];
        renderCaseList(lists.positive, positive, 'positive');
        renderCaseList(lists.negative, negative, 'negative');
        renderCaseList(lists.edge, edge, 'edge');
        renderAssertions(lists.assertions, assertions);
        renderProviderBadge(data._provider);
        updateResultTabCounts({
            All: positive.length + negative.length + edge.length + assertions.length,
            Positive: positive.length,
            Negative: negative.length,
            Edge: edge.length,
            Assertions: assertions.length,
        });
        setResultCategory('all');
    }

    // --- Result category tabs ---
    const resultGroups = document.querySelectorAll('#resultsGroups .result-group');
    const resultsGroupsEl = document.getElementById('resultsGroups');

    function updateResultTabCounts(counts) {
        Object.entries(counts).forEach(([label, n]) => {
            const el = document.getElementById('rtc' + label);
            if (el) el.textContent = n;
        });
    }

    function setResultCategory(cat) {
        document.querySelectorAll('#resultTabs .result-tab').forEach((t) => {
            t.classList.toggle('active', t.dataset.cat === cat);
        });
        resultGroups.forEach((g) => {
            g.classList.toggle('hidden', cat !== 'all' && g.dataset.cat !== cat);
        });
        if (resultsGroupsEl) resultsGroupsEl.classList.toggle('single-cat', cat !== 'all');
    }

    document.querySelectorAll('#resultTabs .result-tab').forEach((tab) => {
        tab.addEventListener('click', () => setResultCategory(tab.dataset.cat));
    });

    // --- Export dropdown ---
    const exportMenuBtn = document.getElementById('exportMenuBtn');
    const exportMenu = document.getElementById('exportMenu');
    if (exportMenuBtn && exportMenu) {
        exportMenuBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            const nowHidden = exportMenu.classList.toggle('hidden');
            exportMenuBtn.setAttribute('aria-expanded', String(!nowHidden));
        });
        document.addEventListener('click', () => {
            if (!exportMenu.classList.contains('hidden')) {
                exportMenu.classList.add('hidden');
                exportMenuBtn.setAttribute('aria-expanded', 'false');
            }
        });
    }

    function renderCaseList(container, cases, type) {
        container.innerHTML = '';
        if (!cases.length) {
            container.innerHTML = '<p class="case-desc empty-group">No cases generated.</p>';
            return;
        }

        cases.forEach((c) => {
            const el = document.createElement('div');
            el.className = 'case-item';

            const statusCode = c.expected?.status_code;
            const statusTag = statusCode ? `<span class="tag status">HTTP ${statusCode}</span>` : '';

            let extraTags = '';
            if (c.request?.method && c.request.method !== document.getElementById('method').value) {
                extraTags += `<span class="tag">${escapeHtml(c.request.method)}</span>`;
            }

            const validationRules = c.expected?.validation_rules;
            let rulesHtml = '';
            if (Array.isArray(validationRules) && validationRules.length) {
                rulesHtml = `<ul class="case-rules">${validationRules.map(r => `<li>${escapeHtml(r)}</li>`).join('')}</ul>`;
            }

            el.innerHTML = `
                <div class="case-row">
                    <span class="status-badge pending">Pending</span>
                    <span class="case-id">${escapeHtml(c.id || '')}</span>
                    <span class="case-title">${escapeHtml(c.title || 'Untitled')}</span>
                    <span class="case-row-tags">${statusTag}${extraTags}</span>
                    <button type="button" class="btn-run">Run</button>
                    <span class="chevron">&#9662;</span>
                </div>
                <div class="case-body hidden">
                    <div class="case-desc">${escapeHtml(c.description || '')}</div>
                    ${rulesHtml}
                    <div class="case-details hidden"></div>
                </div>
            `;
            container.appendChild(el);
            registerCaseCard(el, c, type);
        });
    }

    function renderAssertions(container, assertions) {
        container.innerHTML = '';
        if (!assertions.length) {
            container.innerHTML = '<p class="case-desc empty-group">No assertions generated.</p>';
            return;
        }

        assertions.forEach((a) => {
            const el = document.createElement('div');
            el.className = 'case-item';

            const severity = (a.severity || 'medium').toLowerCase();
            const severityTag = `<span class="tag severity-${severity}">${escapeHtml(a.severity || 'medium')}</span>`;
            const categoryTag = `<span class="tag">${escapeHtml(a.category || 'general')}</span>`;

            el.innerHTML = `
                <div class="case-row">
                    <span class="status-badge pending">Pending</span>
                    <span class="case-title">${escapeHtml(a.rule || 'Untitled assertion')}</span>
                    <span class="case-row-tags">${categoryTag}${severityTag}</span>
                    <button type="button" class="btn-run">Run</button>
                    <span class="chevron">&#9662;</span>
                </div>
                <div class="case-body hidden">
                    <div class="case-details hidden"></div>
                </div>
            `;
            container.appendChild(el);
            registerCaseCard(el, a, 'assertion');
        });
    }

    function escapeHtml(str) {
        if (typeof str !== 'string') return String(str || '');
        return str
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }
});
