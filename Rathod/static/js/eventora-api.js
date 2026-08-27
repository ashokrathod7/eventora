/* Eventora local API — no Firebase service is used. */
(function(){
const listeners=new Map(), authCallbacks=[]; let currentUser=null;
async function req(url,opt={}){const o={...opt,headers:{'Content-Type':'application/json',...(opt.headers||{})}};const r=await fetch(url,o);const d=await r.json().catch(()=>({}));if(!r.ok){const e=new Error(d.message||'Request failed');e.code=d.code||'server/error';throw e;}return d;}
class Snapshot{constructor(v,p){this._value=v;this.path=p;}val(){return this._value;}exists(){return this._value!==null&&this._value!==undefined;}}
class Ref{
 constructor(p){this.path=(p||'').replace(/^\/+|\/+$/g,'');}
 child(k){return new Ref(this.path?this.path+'/'+k:String(k));}
 set(v){return req('/api/data/'+encodeURIComponent(this.path),{method:'PUT',body:JSON.stringify({value:v})}).then(x=>{notify(this.path);return x;});}
 update(v){return req('/api/data/'+encodeURIComponent(this.path),{method:'PATCH',body:JSON.stringify({value:v})}).then(x=>{notify(this.path);return x;});}
 remove(){return req('/api/data/'+encodeURIComponent(this.path),{method:'DELETE'}).then(x=>{notify(this.path);return x;});}
 once(){return req('/api/data/'+encodeURIComponent(this.path)).then(x=>new Snapshot(x.value,this.path));}
 on(ev,cb,err){if(ev!=='value')return cb;if(!listeners.has(this.path))listeners.set(this.path,new Set());listeners.get(this.path).add(cb);this.once().then(cb).catch(e=>err&&err(e));return cb;}
 off(ev,cb){const s=listeners.get(this.path);if(!s)return;if(cb)s.delete(cb);else s.clear();}
 transaction(fn){return this.once().then(s=>{const n=fn(s.val());return this.set(n).then(()=>({snapshot:new Snapshot(n,this.path)}));});}
 push(){return this.child(Date.now()+'_'+Math.random().toString(36).slice(2,8));}
}
function decorateUser(u){if(!u)return null;return {...u,updatePassword:(newPass)=>req('/api/auth/change-password',{method:'POST',body:JSON.stringify({old_password:window.__eventoraOldPassword||'',new_password:newPass})})};}
function notify(path){listeners.forEach((set,p)=>{if(path===p||path.startsWith(p+'/')||p.startsWith(path+'/'))new Ref(p).once().then(s=>set.forEach(cb=>cb(s))).catch(()=>{});});}
const auth={
get currentUser(){return currentUser;},
onAuthStateChanged(cb){authCallbacks.push(cb);req('/api/auth/me').then(x=>{currentUser=decorateUser(x.user||null);cb(currentUser);}).catch(()=>cb(null));return()=>{const i=authCallbacks.indexOf(cb);if(i>=0)authCallbacks.splice(i,1);};},
signInWithEmailAndPassword(email,password){return req('/api/auth/login',{method:'POST',body:JSON.stringify({email,password})}).then(x=>{currentUser=decorateUser(x.user);authCallbacks.forEach(cb=>cb(currentUser));return{user:currentUser};});},
createUserWithEmailAndPassword(email,password,profile={}){return req('/api/auth/register',{method:'POST',body:JSON.stringify({email,password,name:profile.name||'',phone:profile.phone||''})}).then(x=>{currentUser=decorateUser(x.user);authCallbacks.forEach(cb=>cb(currentUser));return{user:currentUser};});},
signOut(){return req('/api/auth/logout',{method:'POST'}).then(()=>{currentUser=null;authCallbacks.forEach(cb=>cb(null));});},
sendPasswordResetEmail(email){return req('/api/auth/forgot',{method:'POST',body:JSON.stringify({email})});}
};
const storage={
  ref(path){
    return {
      put(file){
        const task={snapshot:{ref:null}, on(type,progress,error,complete){
          (async()=>{
            try{
              const fd=new FormData();
              fd.append('file',file);
              fd.append('path',path);
              const r=await fetch('/api/upload',{method:'POST',body:fd});
              const d=await r.json().catch(()=>({}));
              if(!r.ok) throw new Error(d.message||'Upload failed');
              task.snapshot.ref._url=d.url||'';
              if(progress) progress({bytesTransferred:file.size,totalBytes:file.size});
              if(complete) complete();
            }catch(e){
              if(error) error(e);
            }
          })();
          return task;
        }};
        task.snapshot.ref={getDownloadURL:()=>Promise.resolve(task.snapshot.ref._url||'')};
        return task;
      }
    };
  }
};
window.eventoraAuth=auth;window.eventoraDB={ref:p=>new Ref(p)};window.eventoraStorage=storage;
})();