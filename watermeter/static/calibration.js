// Calibration tab: collection session, calibration job, per-dial preview + manual pivot.
(function () {
    'use strict';

    const FAST_POLL_MS = 3000;
    const SLOW_POLL_MS = 30000;
    const SOURCE_LABELS = {
        needle_axes: ['measured', 'ok'],
        parallax: ['parallax model', 'info'],
        manual: ['manual', 'info'],
        tick_centre: ['scale centre', 'warn'],
    };

    let status = null;
    let pollTimer = null;
    let pivotEdit = null; // {id, x, y} while placing a pivot
    let previewStamp = Date.now();

    const $ = (id) => document.getElementById(id);

    function showMessage(text, isError) {
        const el = $('cal-message');
        el.textContent = text;
        el.className = 'cal-message ' + (isError ? 'cal-message-error' : 'cal-message-ok');
        el.hidden = false;
        clearTimeout(showMessage.timer);
        showMessage.timer = setTimeout(() => { el.hidden = true; }, 6000);
    }

    async function api(method, url, body) {
        const opts = { method, headers: {} };
        if (body !== undefined) {
            opts.headers['Content-Type'] = 'application/json';
            opts.body = JSON.stringify(body);
        }
        const resp = await fetch(url, opts);
        let data = {};
        try { data = await resp.json(); } catch (e) { /* empty body */ }
        if (!resp.ok || data.success === false) {
            throw new Error(data.message || ('HTTP ' + resp.status));
        }
        return data;
    }

    function fmtDuration(ms) {
        const min = Math.max(0, Math.floor(ms / 60000));
        const h = Math.floor(min / 60);
        return h ? `${h} h ${min % 60} min` : `${min} min`;
    }

    function fmtDate(iso) {
        if (!iso) return '–';
        return new Date(iso).toLocaleString([], { dateStyle: 'short', timeStyle: 'short' });
    }

    function jobBusy(st) {
        return st && st.session.job && st.session.job.state === 'running';
    }

    // --- rendering -----------------------------------------------------------------------------

    function renderBadge(st) {
        const badge = $('cal-badge');
        let text = 'Not calibrated', cls = 'cal-badge-idle';
        if (jobBusy(st)) { text = 'Calibrating…'; cls = 'cal-badge-busy'; }
        else if (st.session.collecting) { text = 'Collecting'; cls = 'cal-badge-busy'; }
        else if (st.calibration) { text = 'Calibrated'; cls = 'cal-badge-ok'; }
        badge.textContent = text;
        badge.className = 'cal-badge ' + cls;
    }

    function renderStatus(st) {
        const body = $('status-body');
        body.replaceChildren();
        const cal = st.calibration;
        const lines = [];
        if (!cal) {
            lines.push(['Not calibrated yet. Collect frames below; the calibration runs when the target is reached.', '']);
        } else {
            const ids = st.analog_ids;
            const failed = (cal.report && cal.report.failed) || {};
            const active = ids.filter((id) => cal.dials[id] && !cal.dials[id].stale).length;
            lines.push([`Calibrated ${fmtDate(cal.created_at)} · ${active}/${ids.length} dials active`, '']);
            const rep = cal.report || {};
            if (rep.opencv && rep.calibrated && rep.opencv.mae != null && rep.calibrated.mae_after != null) {
                lines.push([`Dial consistency error: OpenCV ${rep.opencv.mae.toFixed(3)} → calibrated ${rep.calibrated.mae_after.toFixed(3)}`, '']);
            }
            ids.forEach((id) => {
                const d = cal.dials[id];
                if (failed[id]) lines.push([`${id}: calibration failed (${failed[id]}) – OpenCV fallback`, 'warn']);
                else if (!d) lines.push([`${id}: not calibrated – OpenCV fallback`, 'warn']);
                else if (d.stale) lines.push([`${id}: ROI changed since calibrating – please recalibrate (OpenCV fallback)`, 'warn']);
            });
        }
        const job = st.session.job || {};
        if (job.state === 'failed' && job.message) lines.push([`Last calibration failed: ${job.message}`, 'warn']);
        lines.forEach(([text, kind]) => {
            const p = document.createElement('p');
            p.textContent = text;
            if (kind) p.className = 'cal-warn';
            body.appendChild(p);
        });
    }

    function renderCollect(st) {
        const s = st.session;
        $('collect-idle').hidden = s.collecting;
        $('collect-running').hidden = !s.collecting;
        $('collect-start').disabled = jobBusy(st);
        if (!s.collecting || !s.target) return;
        const elapsed = Date.now() - new Date(s.started_at).getTime();
        let pct, text;
        if (s.target.type === 'frames') {
            pct = s.frames / s.target.value;
            text = `${s.frames} / ${s.target.value} frames`;
        } else {
            pct = elapsed / (s.target.value * 3600000);
            text = `${fmtDuration(elapsed)} / ${s.target.value} h · ${s.frames} frames`;
        }
        if (s.target.type === 'frames') text += ` · running ${fmtDuration(elapsed)}`;
        if (s.frames_on_disk > s.frames) text += ` · ${s.frames_on_disk - s.frames} not aligned`;
        $('collect-bar').style.width = Math.min(100, Math.round(pct * 100)) + '%';
        $('collect-text').textContent = text;
    }

    function renderRun(st) {
        const s = st.session, job = s.job || {};
        const busy = jobBusy(st);
        $('run-btn').disabled = busy || s.frames_on_disk < s.min_frames;
        $('run-progress').hidden = !busy;
        let text = '';
        if (busy) {
            const p = job.progress;
            if (p && p.total) {
                const label = p.stage === 'align' ? 'Aligning frames' : 'Measuring dials';
                text = `${label} ${p.done}/${p.total}…`;
                const pct = p.stage === 'align' ? 0.9 * p.done / p.total : 0.9 + 0.1 * p.done / p.total;
                $('run-bar').style.width = Math.round(pct * 100) + '%';
            } else {
                text = 'Starting…';
                $('run-bar').style.width = '2%';
            }
        } else if (s.frames_on_disk < s.min_frames) {
            text = `${s.frames_on_disk} frames collected – at least ${s.min_frames} needed`;
        } else if (job.state === 'done') {
            text = `Last run finished ${fmtDate(job.finished_at)} · ${s.frames_on_disk} frames available`;
        } else {
            text = `${s.frames_on_disk} frames available`;
        }
        $('run-text').textContent = text;
    }

    function dialCard(id, st) {
        const cal = st.calibration;
        const d = cal ? cal.dials[id] : null;
        const card = document.createElement('div');
        card.className = 'cal-dial-card';
        card.dataset.id = id;

        const title = document.createElement('div');
        title.className = 'cal-dial-title';
        title.textContent = id;
        card.appendChild(title);

        const wrap = document.createElement('div');
        wrap.className = 'cal-preview-wrap';
        const img = document.createElement('img');
        img.className = 'cal-preview';
        img.alt = `${id} preview`;
        img.src = `/api/calibration/preview/${id}.jpg?t=${previewStamp}`;
        img.onerror = () => { wrap.classList.add('cal-preview-missing'); };
        wrap.appendChild(img);
        if (pivotEdit && pivotEdit.id === id) {
            wrap.classList.add('cal-preview-editing');
            img.addEventListener('click', (ev) => placePivot(ev, img, wrap));
            if (pivotEdit.x != null) wrap.appendChild(marker(img, pivotEdit.x, pivotEdit.y));
        }
        card.appendChild(wrap);

        const info = document.createElement('div');
        info.className = 'cal-dial-info';
        const value = (d && d.value) || '–';
        const parts = [`Value ${value}`];
        if (d) parts.push(`${d.ticks} ticks`, `offset ${Number(d.offset).toFixed(3)}`);
        info.textContent = parts.join(' · ');
        card.appendChild(info);

        if (d) {
            const [label, kind] = SOURCE_LABELS[d.pivot_source] || [d.pivot_source, 'info'];
            const badge = document.createElement('span');
            badge.className = `cal-source cal-source-${kind}`;
            badge.textContent = `Pivot: ${label}`;
            card.appendChild(badge);
            if (d.stale) {
                const w = document.createElement('div');
                w.className = 'cal-warn';
                w.textContent = 'ROI changed – recalibrate';
                card.appendChild(w);
            }
        }

        const actions = document.createElement('div');
        actions.className = 'cal-dial-actions';
        const busy = jobBusy(st);
        if (pivotEdit && pivotEdit.id === id) {
            const apply = button('Apply', 'btn btn-primary', () => applyPivot(id));
            apply.disabled = pivotEdit.x == null || busy;
            actions.append(apply, button('Cancel', 'btn btn-secondary', () => { pivotEdit = null; render(); }));
        } else {
            const set = button('Set pivot', 'btn btn-secondary', () => { pivotEdit = { id, x: null, y: null }; render(); });
            set.disabled = busy || pivotEdit !== null;
            actions.appendChild(set);
            if (d && d.pivot_source === 'manual') {
                const auto = button('Automatic', 'btn btn-secondary', () => clearPivot(id));
                auto.disabled = busy;
                actions.appendChild(auto);
            }
        }
        card.appendChild(actions);
        return card;
    }

    function button(text, cls, onClick) {
        const b = document.createElement('button');
        b.textContent = text;
        b.className = cls;
        b.addEventListener('click', onClick);
        return b;
    }

    function marker(img, x, y) {
        const m = document.createElement('div');
        m.className = 'cal-pivot-marker';
        const place = () => {
            const sx = img.clientWidth / img.naturalWidth, sy = img.clientHeight / img.naturalHeight;
            m.style.left = (x * sx) + 'px';
            m.style.top = (y * sy) + 'px';
        };
        if (img.complete && img.naturalWidth) place(); else img.addEventListener('load', place);
        return m;
    }

    function renderDials(st) {
        const grid = $('dial-grid');
        grid.replaceChildren(...st.analog_ids.map((id) => dialCard(id, st)));
    }

    function render() {
        if (!status) return;
        renderBadge(status);
        renderStatus(status);
        renderCollect(status);
        renderRun(status);
        renderDials(status);
    }

    // --- actions -------------------------------------------------------------------------------

    function placePivot(ev, img, wrap) {
        const rect = img.getBoundingClientRect();
        pivotEdit.x = Math.round((ev.clientX - rect.left) * img.naturalWidth / rect.width);
        pivotEdit.y = Math.round((ev.clientY - rect.top) * img.naturalHeight / rect.height);
        render();
    }

    async function applyPivot(id) {
        try {
            await api('POST', `/api/calibration/pivot/${id}`, { x: pivotEdit.x, y: pivotEdit.y });
            pivotEdit = null;
            showMessage(`Pivot for ${id} set – recalibrating…`, false);
        } catch (e) {
            showMessage(`Could not set pivot: ${e.message}`, true);
        }
        poll();
    }

    async function clearPivot(id) {
        try {
            await api('DELETE', `/api/calibration/pivot/${id}`);
            showMessage(`${id} back to the automatic pivot – recalibrating…`, false);
        } catch (e) {
            showMessage(`Could not reset pivot: ${e.message}`, true);
        }
        poll();
    }

    async function startCollecting() {
        const type = document.querySelector('input[name="target-type"]:checked').value;
        const value = Number($(type === 'hours' ? 'target-hours' : 'target-frames').value);
        try {
            await api('POST', '/api/calibration/collect/start', { type, value });
            showMessage('Collecting started.', false);
        } catch (e) {
            showMessage(`Could not start: ${e.message}`, true);
        }
        poll();
    }

    async function stopCollecting() {
        try {
            await api('POST', '/api/calibration/collect/stop');
            showMessage('Collecting stopped. The frames are kept.', false);
        } catch (e) {
            showMessage(`Could not stop: ${e.message}`, true);
        }
        poll();
    }

    async function runCalibration() {
        try {
            await api('POST', '/api/calibration/run');
            showMessage('Calibration started.', false);
        } catch (e) {
            showMessage(`Could not start calibration: ${e.message}`, true);
        }
        poll();
    }

    // --- polling -------------------------------------------------------------------------------

    async function poll() {
        clearTimeout(pollTimer);
        try {
            const data = await api('GET', '/api/calibration/status');
            if (!pivotEdit) previewStamp = Date.now(); // new reading every cycle: refresh the previews
            status = data;
            if (pivotEdit) {
                // keep the image stable while placing a pivot; only refresh the other sections
                renderBadge(status); renderStatus(status); renderCollect(status); renderRun(status);
            } else {
                render();
            }
        } catch (e) {
            showMessage(`Status unavailable: ${e.message}`, true);
        }
        const fast = status && (status.session.collecting || jobBusy(status));
        pollTimer = setTimeout(poll, fast ? FAST_POLL_MS : SLOW_POLL_MS);
    }

    document.addEventListener('DOMContentLoaded', () => {
        $('collect-start').addEventListener('click', startCollecting);
        $('collect-stop').addEventListener('click', stopCollecting);
        $('run-btn').addEventListener('click', runCalibration);
        poll();
    });
})();
