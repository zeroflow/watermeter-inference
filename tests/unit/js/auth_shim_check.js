'use strict';
// Behavior check for watermeter/static/auth.js. Usage: node auth_shim_check.js <path to auth.js>
// The script runs in a vm sandbox with a fake window, location, fetch and XMLHttpRequest.

const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

const authPath = process.argv[2];
if (!authPath) {
    console.error('usage: node auth_shim_check.js <path to auth.js>');
    process.exit(2);
}
const source = fs.readFileSync(authPath, 'utf8');
const ORIGIN = 'http://meter.example.lan:8001';

function makeEnv(opts) {
    opts = opts || {};
    const state = { assigned: [], fetchCalls: [], xhrs: [], nextStatus: 200 };
    const pathname = opts.pathname || '/training';
    const search = opts.search || '';
    const location = {
        origin: ORIGIN,
        href: ORIGIN + pathname + search,
        pathname: pathname,
        search: search,
        assign: function (url) { state.assigned.push(url); },
    };

    class FakeXHR {
        constructor() {
            this.headers = [];
            this.listeners = {};
            this.status = 0;
            this.responseHeaders = {};
            state.xhrs.push(this);
        }
        open(method, url) { this.method = method; this.url = url; }
        send() { this.sent = true; }
        setRequestHeader(name, value) { this.headers.push([name, value]); }
        addEventListener(type, callback) { (this.listeners[type] = this.listeners[type] || []).push(callback); }
        getResponseHeader(name) {
            const value = this.responseHeaders[name.toLowerCase()];
            return value === undefined ? null : value;
        }
        finish(status, responseHeaders) {
            this.status = status;
            this.responseHeaders = responseHeaders || {};
            (this.listeners.loadend || []).forEach((callback) => callback.call(this));
        }
        header(name) {
            const hit = this.headers.filter((pair) => pair[0].toLowerCase() === name.toLowerCase());
            return hit.length ? hit[0][1] : null;
        }
    }

    const sandbox = {
        location: location,
        URL: URL,
        Headers: Headers,
        Request: Request,
        XMLHttpRequest: FakeXHR,
        console: console,
        fetch: function (input, init) {
            state.fetchCalls.push({ input: input, init: init });
            return Promise.resolve({ status: state.nextStatus });
        },
    };
    sandbox.window = sandbox;
    sandbox.globalThis = sandbox;
    vm.createContext(sandbox);
    sandbox.__load = function () { vm.runInContext(source, sandbox, { filename: authPath }); };
    sandbox.__load();
    return { sandbox: sandbox, state: state, FakeXHR: FakeXHR };
}

function headerOf(init, name) {
    assert.ok(init && init.headers, 'init.headers expected');
    return init.headers.get(name);
}

async function main() {
    // Guard flag: loading twice does not wrap twice.
    {
        const env = makeEnv();
        const wrapped = env.sandbox.fetch;
        const open = env.FakeXHR.prototype.open;
        const send = env.FakeXHR.prototype.send;
        assert.strictEqual(env.sandbox.__wmAuthShim, true);
        env.sandbox.__load();
        assert.strictEqual(env.sandbox.fetch, wrapped, 'fetch wrapped twice');
        assert.strictEqual(env.FakeXHR.prototype.open, open, 'XHR open wrapped twice');
        assert.strictEqual(env.FakeXHR.prototype.send, send, 'XHR send wrapped twice');
    }

    // fetch: every unsafe method gains the header, existing headers survive.
    for (const method of ['POST', 'PUT', 'PATCH', 'DELETE', 'post']) {
        const env = makeEnv();
        await env.sandbox.fetch('/api/trigger', { method: method, headers: { 'Content-Type': 'application/json' } });
        const init = env.state.fetchCalls[0].init;
        assert.strictEqual(headerOf(init, 'X-Requested-With'), 'watermeter', method);
        assert.strictEqual(headerOf(init, 'Content-Type'), 'application/json', method);
        assert.strictEqual(init.method, method);
    }

    // fetch: the caller's init object is not mutated.
    {
        const env = makeEnv();
        const init = { method: 'POST', headers: { 'Content-Type': 'text/plain' } };
        await env.sandbox.fetch('/api/x', init);
        assert.deepStrictEqual(Object.keys(init.headers), ['Content-Type']);
        assert.strictEqual(init.headers['X-Requested-With'], undefined);
    }

    // fetch: POST without init headers, and an absolute same-origin string URL.
    {
        const env = makeEnv();
        await env.sandbox.fetch(ORIGIN + '/api/x', { method: 'POST', body: 'a=1' });
        const init = env.state.fetchCalls[0].init;
        assert.strictEqual(headerOf(init, 'x-requested-with'), 'watermeter');
        assert.strictEqual(init.body, 'a=1');
    }

    // fetch: URL object input.
    {
        const env = makeEnv();
        await env.sandbox.fetch(new URL('/api/x', ORIGIN), { method: 'PUT' });
        assert.strictEqual(headerOf(env.state.fetchCalls[0].init, 'X-Requested-With'), 'watermeter');
    }

    // fetch: Request object input keeps its body and headers and gains the header.
    {
        const env = makeEnv();
        const request = new Request(ORIGIN + '/api/x', {
            method: 'POST',
            body: 'payload',
            headers: { 'Content-Type': 'text/plain' },
        });
        await env.sandbox.fetch(request);
        const sent = env.state.fetchCalls[0].input;
        assert.ok(sent instanceof Request);
        assert.strictEqual(sent.method, 'POST');
        assert.strictEqual(sent.headers.get('X-Requested-With'), 'watermeter');
        assert.strictEqual(sent.headers.get('Content-Type'), 'text/plain');
        assert.strictEqual(await sent.text(), 'payload');
        assert.strictEqual(env.state.fetchCalls[0].init, undefined);
    }

    // fetch: GET and cross-origin requests are not modified.
    {
        const env = makeEnv();
        const getInit = { headers: { Accept: 'application/json' } };
        await env.sandbox.fetch('/api/status', getInit);
        assert.strictEqual(env.state.fetchCalls[0].init, getInit);
        await env.sandbox.fetch('/api/status');
        assert.strictEqual(env.state.fetchCalls[1].init, undefined);
        const crossInit = { method: 'POST', headers: { 'Content-Type': 'text/plain' } };
        await env.sandbox.fetch('http://other.example.lan/api/x', crossInit);
        assert.strictEqual(env.state.fetchCalls[2].init, crossInit);
        await env.sandbox.fetch('http://meter.example.lan:9999/api/x', { method: 'POST' });
        assert.strictEqual(env.state.fetchCalls[3].init.headers, undefined);
    }

    // fetch: a same-origin 401 redirects to the login page and still resolves to the response.
    {
        const env = makeEnv({ pathname: '/training', search: '?tab=a b' });
        env.state.nextStatus = 401;
        const response = await env.sandbox.fetch('/api/status');
        assert.strictEqual(response.status, 401);
        assert.deepStrictEqual(env.state.assigned, ['/login?next=' + encodeURIComponent('/training?tab=a b')]);
        await env.sandbox.fetch('/api/status');
        assert.strictEqual(env.state.assigned.length, 1, 'redirect issued twice');
    }

    // fetch: no redirect on /login or /setup, and none for a cross-origin 401 or a non-401 error.
    for (const page of ['/login', '/setup']) {
        const env = makeEnv({ pathname: page });
        env.state.nextStatus = 401;
        await env.sandbox.fetch('/api/status');
        assert.deepStrictEqual(env.state.assigned, [], page);
    }
    {
        const env = makeEnv();
        env.state.nextStatus = 401;
        await env.sandbox.fetch('http://other.example.lan/api/status');
        assert.deepStrictEqual(env.state.assigned, []);
        env.state.nextStatus = 403;
        await env.sandbox.fetch('/api/status');
        env.state.nextStatus = 500;
        await env.sandbox.fetch('/api/status');
        assert.deepStrictEqual(env.state.assigned, []);
    }

    // XHR: unsafe same-origin requests gain the header, safe and cross-origin ones do not.
    for (const method of ['POST', 'PUT', 'PATCH', 'DELETE']) {
        const env = makeEnv();
        const xhr = new env.sandbox.XMLHttpRequest();
        xhr.open(method, '/api/x');
        xhr.send();
        assert.strictEqual(xhr.header('X-Requested-With'), 'watermeter', method);
        assert.strictEqual(xhr.sent, true);
    }
    {
        const env = makeEnv();
        const get = new env.sandbox.XMLHttpRequest();
        get.open('GET', '/api/status');
        get.send();
        assert.strictEqual(get.header('X-Requested-With'), null);
        const cross = new env.sandbox.XMLHttpRequest();
        cross.open('POST', 'http://other.example.lan/api/x');
        cross.send();
        assert.strictEqual(cross.header('X-Requested-With'), null);
        assert.strictEqual(cross.sent, true);
        cross.finish(401);
        assert.deepStrictEqual(env.state.assigned, []);
    }

    // XHR: a same-origin 401 redirects; htmx's HX-Redirect response is left to htmx.
    {
        const env = makeEnv({ pathname: '/', search: '?x=1' });
        const plain = new env.sandbox.XMLHttpRequest();
        plain.open('GET', '/api/status/html');
        plain.send();
        plain.finish(200);
        assert.deepStrictEqual(env.state.assigned, []);
        plain.finish(401);
        assert.deepStrictEqual(env.state.assigned, ['/login?next=' + encodeURIComponent('/?x=1')]);
    }
    {
        const env = makeEnv();
        const htmx = new env.sandbox.XMLHttpRequest();
        htmx.open('GET', '/api/status/html');
        htmx.send();
        htmx.finish(401, { 'hx-redirect': '/login?next=%2Ftraining' });
        assert.deepStrictEqual(env.state.assigned, []);
    }
    for (const page of ['/login', '/setup']) {
        const env = makeEnv({ pathname: page });
        const xhr = new env.sandbox.XMLHttpRequest();
        xhr.open('POST', '/api/x');
        xhr.send();
        xhr.finish(401);
        assert.deepStrictEqual(env.state.assigned, [], page);
    }

    console.log('auth.js shim: all checks passed');
}

main().catch((error) => {
    console.error(error);
    process.exit(1);
});
