/* sweeper.js — Progressive SPA enhancement for the iPracticom dashboard.
 *
 * Loaded by base_spa.html as a deferred script. Zero dependencies, no build
 * step, no JS framework. Forms that opt in via `data-spa-action` POST via
 * fetch + show a toast instead of doing a full-page reload.
 *
 * Public API (window.sweeper):
 *   - post(url, formData, opts) → Promise<{ok, status, body}>
 *   - toast(message, kind)      → void   kind: "ok" | "err" | "info"
 *
 * CSRF: sends an Origin header on every POST so the v1.5.9 CSRF check
 * (_csrf_origin_ok) accepts the request. Same-origin requests always match.
 *
 * No external CDN deps — Tailwind is already loaded by base_spa.html.
 */

(function () {
  'use strict';

  var TOAST_TTL_MS = 4200;

  function getMetaCsrf() {
    var el = document.querySelector('meta[name="csrf-token"]');
    return el ? el.getAttribute('content') || '' : '';
  }

  function post(url, formData, opts) {
    opts = opts || {};
    var headers = {
      'Accept': 'application/json',
      'X-Requested-With': 'XMLHttpRequest',
    };
    var token = getMetaCsrf();
    if (token) {
      headers['X-CSRF-Token'] = token;
    }
    // Origin header — browsers normally set it automatically; we include it
    // explicitly so the CSRF check on the server can compare against the
    // trusted list without depending on browser quirks in test clients.
    if (location.origin) {
      headers['Origin'] = location.origin;
    }

    var body;
    var contentType;
    if (formData instanceof FormData) {
      body = formData;
      // Let the browser set multipart boundary — don't override Content-Type.
    } else if (formData && typeof formData === 'object') {
      body = new URLSearchParams();
      for (var k in formData) {
        if (Object.prototype.hasOwnProperty.call(formData, k)) {
          body.append(k, formData[k]);
        }
      }
      contentType = 'application/x-www-form-urlencoded; charset=UTF-8';
      headers['Content-Type'] = contentType;
    } else {
      body = '';
    }

    return fetch(url, {
      method: opts.method || 'POST',
      credentials: 'same-origin',
      headers: headers,
      body: body,
      redirect: opts.followRedirect ? 'follow' : 'manual',
    }).then(function (resp) {
      // 2xx → try JSON
      if (resp.ok) {
        return resp.text().then(function (txt) {
          var body = null;
          if (txt && /json/i.test(resp.headers.get('Content-Type') || '')) {
            try { body = JSON.parse(txt); } catch (e) { body = txt; }
          } else {
            body = txt || null;
          }
          return { ok: true, status: resp.status, body: body, headers: resp.headers };
        });
      }
      // opaqueredirect = server returned 3xx and we followed 'manual' — the
      // action happened server-side, the redirect is just navigation. Treat
      // as success so the caller can do its reload/redirect.
      if (resp.type === 'opaqueredirect') {
        return { ok: true, status: 302, body: null, headers: resp.headers, redirected: true };
      }
      // 4xx/5xx → still try to parse error body
      return resp.text().then(function (txt) {
        var body = null;
        try { body = JSON.parse(txt); } catch (e) { body = { raw: txt }; }
        return { ok: false, status: resp.status, body: body, headers: resp.headers };
      });
    });
  }

  function toast(message, kind) {
    kind = kind || 'info';
    var host = document.getElementById('toast-host');
    if (!host) return;
    var colorClass =
      kind === 'ok' ? 'bg-emerald-600' :
      kind === 'err' ? 'bg-rose-600' :
      'bg-slate-700';
    var node = document.createElement('div');
    node.setAttribute('role', 'status');
    node.className = [
      'pointer-events-auto', 'rounded-xl', 'shadow-lg', 'border',
      'border-white/10', 'text-white', 'text-sm', 'px-4', 'py-3',
      'min-w-[240px]', 'max-w-md', 'animate-fade-in',
      colorClass
    ].join(' ');
    node.textContent = String(message);
    host.appendChild(node);
    setTimeout(function () {
      node.style.transition = 'opacity 200ms ease';
      node.style.opacity = '0';
      setTimeout(function () { node.remove(); }, 220);
    }, TOAST_TTL_MS);
  }

  function bindForm(form) {
    if (form.__sweeperBound) return;
    form.__sweeperBound = true;
    form.addEventListener('submit', function (ev) {
      ev.preventDefault();
      var url = form.getAttribute('data-spa-action') ||
                form.getAttribute('action') ||
                location.pathname;
      var fd = new FormData(form);
      var successMsg = form.getAttribute('data-spa-success') || 'בוצע';
      var redirect = form.getAttribute('data-spa-redirect');
      post(url, fd).then(function (result) {
        if (result.ok) {
          toast(successMsg, 'ok');
          if (redirect) {
            // Allow server-driven redirect targets (e.g. /history)
            setTimeout(function () { location.assign(redirect); }, 400);
          } else if (form.getAttribute('data-spa-reload') === '1') {
            setTimeout(function () { location.reload(); }, 400);
          }
        } else {
          var err = (result.body && (result.body.error || result.body.message)) ||
                     ('HTTP ' + result.status);
          toast('שגיאה: ' + err, 'err');
        }
      }).catch(function (e) {
        toast('שגיאת רשת: ' + (e && e.message || 'unknown'), 'err');
      });
    });
  }

  function bindAll() {
    var forms = document.querySelectorAll('form[data-spa-action]');
    for (var i = 0; i < forms.length; i++) bindForm(forms[i]);
  }

  // Expose public API
  window.sweeper = {
    post: post,
    toast: toast,
    bindAll: bindAll,
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bindAll);
  } else {
    bindAll();
  }
})();