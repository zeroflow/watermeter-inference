/*
 * Browser shim for the admin login. Loaded on every page that is behind the gate.
 *
 *  1. Adds "X-Requested-With: watermeter" to same-origin unsafe requests made with fetch() and XMLHttpRequest. The
 *     server requires it on cookie-authenticated /api writes, and a foreign page cannot set it without a CORS
 *     preflight that the gate refuses. htmx always uses XMLHttpRequest, so it is covered without any change.
 *  2. Sends the browser to /login?next=<current page> when a same-origin fetch or XHR comes back 401 (for example
 *     because the session expired while a page stayed open). htmx navigates by itself when the 401 carries an
 *     HX-Redirect header, so XHR responses with that header are left alone.
 *
 * Plain ES5, no dependencies, no build step.
 */
(function () {
    'use strict';

    if (window.__wmAuthShim) return;
    window.__wmAuthShim = true;

    var HEADER = 'X-Requested-With';
    var VALUE = 'watermeter';
    var redirecting = false;

    function sameOrigin(url) {
        try {
            return new URL(url, window.location.href).origin === window.location.origin;
        } catch (e) {
            return false;
        }
    }

    function isUnsafe(method) {
        var m = String(method || 'GET').toUpperCase();
        return m !== 'GET' && m !== 'HEAD' && m !== 'OPTIONS';
    }

    function toLogin() {
        var loc = window.location;
        if (redirecting || loc.pathname === '/login' || loc.pathname === '/setup') return;
        redirecting = true;
        loc.assign('/login?next=' + encodeURIComponent(loc.pathname + loc.search));
    }

    function withOptions(init, extra) {
        var merged = {};
        var key;
        if (init) {
            for (key in init) {
                if (Object.prototype.hasOwnProperty.call(init, key)) merged[key] = init[key];
            }
        }
        for (key in extra) {
            if (Object.prototype.hasOwnProperty.call(extra, key)) merged[key] = extra[key];
        }
        return merged;
    }

    // ---- fetch ---------------------------------------------------------------------------------------------------

    var originalFetch = window.fetch;
    if (typeof originalFetch === 'function') {
        window.fetch = function (input, init) {
            var isRequest = typeof Request !== 'undefined' && input instanceof Request;
            var url;
            if (isRequest) {
                url = input.url;
            } else if (typeof URL !== 'undefined' && input instanceof URL) {
                url = input.href;
            } else {
                url = String(input);
            }
            var method = (init && init.method) || (isRequest && input.method) || 'GET';
            var same = sameOrigin(url);

            if (same && isUnsafe(method)) {
                // Headers given in init replace the Request's own headers, exactly as fetch() itself does.
                var base = init && init.headers !== undefined ? init.headers : isRequest ? input.headers : undefined;
                var headers = new Headers(base);
                headers.set(HEADER, VALUE);
                if (isRequest) {
                    // Rebuilding the Request keeps its body; init is folded into it so nothing is applied twice.
                    input = new Request(input, withOptions(init, { headers: headers }));
                    init = undefined;
                } else {
                    init = withOptions(init, { headers: headers });
                }
            }

            return originalFetch.call(this, input, init).then(function (response) {
                if (same && response && response.status === 401) toLogin();
                return response;
            });
        };
    }

    // ---- XMLHttpRequest (htmx, the ZIP upload) ---------------------------------------------------------------------

    var proto = window.XMLHttpRequest && window.XMLHttpRequest.prototype;
    if (proto) {
        var originalOpen = proto.open;
        var originalSend = proto.send;

        proto.open = function (method, url) {
            this.__wmAuth = { method: method, url: String(url), listening: false };
            return originalOpen.apply(this, arguments);
        };

        proto.send = function () {
            var xhr = this;
            var info = xhr.__wmAuth;
            if (info && sameOrigin(info.url)) {
                if (isUnsafe(info.method)) xhr.setRequestHeader(HEADER, VALUE);
                if (!info.listening) {
                    info.listening = true;
                    xhr.addEventListener('loadend', function () {
                        if (xhr.status !== 401) return;
                        var hxRedirect = null;
                        try {
                            hxRedirect = xhr.getResponseHeader('HX-Redirect');
                        } catch (e) {
                            hxRedirect = null;
                        }
                        // htmx navigates by itself when the server sent HX-Redirect.
                        if (!hxRedirect) toLogin();
                    });
                }
            }
            return originalSend.apply(this, arguments);
        };
    }
})();
