/*
 Eventora compatibility layer.
 It deliberately does NOT connect to Firebase.
 It implements the small Firebase-like surface used by the original page
 and routes data to the local Flask/SQLite backend.
*/
(function () {
  const listeners = {};
  const authListeners = [];
  let currentUser = null;

  function jsonFetch(url, options) {
    return fetch(url, Object.assign({
      headers: {"Content-Type": "application/json"}
    }, options || {})).then(async r => {
      const data = await r.json().catch(() => ({}));
      if (!r.ok) {
        const e = new Error(data.message || "Server request failed");
        e.code = data.code || "server/error";
        throw e;
      }
      return data;
    });
  }

  class Snapshot {
    constructor(value, path) { this._value = value; this.path = path; }
    val() { return this._value; }
    exists() { return this._value !== null && this._value !== undefined; }
  }

  class Ref {
    constructor(path) { this.path = (path || "").replace(/^\/+|\/+$/g, ""); }
    child(key) { return new Ref(this.path ? this.path + "/" + key : String(key)); }
    set(value) {
      const root = this.path.split("/")[0];
      // Registration/payment records are written by the dedicated Flask endpoints.
      // The legacy Firebase-shaped UI may call set() immediately afterwards;
      // acknowledge that sync call without bypassing server-side authorization.
      if ((root === "registrations" || root === "payments") && !this.path.endsWith(root)) {
        return Promise.resolve({ok:true,value});
      }
      return jsonFetch("/api/data/" + this.path.split("/").map(encodeURIComponent).join("/"), {
        method: "PUT", body: JSON.stringify({value})
      }).then(r => { notify(this.path); return r; });
    }
    update(value) {
      const root = this.path.split("/")[0];
      if ((root === "registrations" || root === "payments") && !this.path.endsWith(root)) {
        return Promise.resolve({ok:true,value});
      }
      return jsonFetch("/api/data/" + this.path.split("/").map(encodeURIComponent).join("/"), {
        method: "PATCH", body: JSON.stringify({value})
      }).then(r => { notify(this.path); return r; });
    }
    remove() {
      return jsonFetch("/api/data/" + this.path.split("/").map(encodeURIComponent).join("/"), {
        method: "DELETE"
      }).then(r => { notify(this.path); return r; });
    }
    once() {
      return jsonFetch("/api/data/" + this.path.split("/").map(encodeURIComponent).join("/"))
        .then(r => new Snapshot(r.value, this.path));
    }
    on(event, callback, errorCallback) {
      if (event !== "value") return;
      if (!listeners[this.path]) listeners[this.path] = [];
      listeners[this.path].push(callback);
      this.once().then(s => callback(s)).catch(e => errorCallback && errorCallback(e));
      return callback;
    }
    off(event, callback) {
      if (!listeners[this.path]) return;
      if (!callback) { delete listeners[this.path]; return; }
      listeners[this.path] = listeners[this.path].filter(x => x !== callback);
    }
    transaction(fn) {
      return this.once().then(s => {
        const next = fn(s.val());
        return this.set(next).then(() => ({snapshot: new Snapshot(next, this.path)}));
      });
    }
    push() {
      const id = String(Date.now()) + "_" + Math.random().toString(36).slice(2, 8);
      return this.child(id);
    }
  }

  function notify(path) {
    Object.keys(listeners).forEach(p => {
      if (path === p || path.startsWith(p + "/") || p.startsWith(path + "/")) {
        listeners[p].forEach(cb => {
          new Ref(p).once().then(cb).catch(()=>{});
        });
      }
    });
  }

  function makeAuth() {
    return {
      get currentUser() { return currentUser; },
      onAuthStateChanged(cb) {
        authListeners.push(cb);
        setTimeout(() => cb(currentUser), 0);
        return () => {
          const i = authListeners.indexOf(cb);
          if (i >= 0) authListeners.splice(i, 1);
        };
      },
      signInWithEmailAndPassword(email, password) {
        return jsonFetch("/api/auth/login", {
          method: "POST", body: JSON.stringify({email, password})
        }).then(r => {
          currentUser = r.user;
          authListeners.forEach(cb => cb(currentUser));
          return {user: currentUser};
        });
      },
      createUserWithEmailAndPassword(email, password, profile) {
        const p = profile || {};
        return jsonFetch("/api/auth/register", {
          method: "POST", body: JSON.stringify({email, password, name: p.name || '', phone: p.phone || ''})
        }).then(r => {
          currentUser = r.user;
          authListeners.forEach(cb => cb(currentUser));
          return {user: currentUser};
        });
      },
      signOut() {
        return jsonFetch("/api/auth/logout", {method:"POST"}).then(() => {
          currentUser = null;
          authListeners.forEach(cb => cb(null));
        });
      },
      sendPasswordResetEmail(email) {
        return jsonFetch("/api/auth/forgot", {
          method:"POST", body:JSON.stringify({email})
        });
      }
    };
  }

  function makeStorage() {
    return {
      ref(path) {
        return {
          put(file) {
            let listeners = [];
            const task = {
              snapshot: { ref: null },
              on(type, progress, error, complete) {
                if (type === "state_changed") {
                  listeners = [progress, error, complete];
                  setTimeout(async () => {
                    try {
                      const dataUrl = await new Promise((resolve, reject) => {
                        const reader = new FileReader();
                        reader.onload = () => resolve(reader.result);
                        reader.onerror = reject;
                        reader.readAsDataURL(file);
                      });
                      task.snapshot.ref._downloadURL = dataUrl;
                      listeners[0] && listeners[0]({
                        bytesTransferred:file.size, totalBytes:file.size
                      });
                      listeners[2] && listeners[2]();
                    } catch(e) { listeners[1] && listeners[1](e); }
                  }, 0);
                }
                return task;
              }
            };
            task.snapshot.ref = {
              getDownloadURL: () => Promise.resolve(task.snapshot.ref._downloadURL || "")
            };
            return task;
          }
        };
      }
    };
  }

  window.firebase = {
    initializeApp: function(){ return {}; },
    auth: makeAuth,
    database: function(){ return {ref: p => new Ref(p)}; },
    storage: makeStorage
  };
})();
