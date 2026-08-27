from flask import Flask, render_template, request, jsonify, session
from werkzeug.security import generate_password_hash, check_password_hash
from pathlib import Path
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
try:
    import razorpay
except Exception:
    razorpay = None
import sqlite3,json,os,secrets,uuid,re,hmac,hashlib,smtplib
try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None
from urllib.parse import urlparse
BASE=Path(__file__).resolve().parent
if load_dotenv:
    load_dotenv(BASE/'.env')
DB=BASE/'instance'/'eventora.db'
UPLOADS=BASE/'static'/'uploads'
ADMIN_EMAIL='rathodashok0103@gmail.com'; LEGACY_ADMIN_EMAIL='admin@eventhubpro.com'; ADMIN_PASSWORD=os.environ.get('EVENTORA_ADMIN_PASSWORD','Admin@12345')
RAZORPAY_KEY_ID=os.environ.get('RAZORPAY_KEY_ID','').strip()
RAZORPAY_KEY_SECRET=os.environ.get('RAZORPAY_KEY_SECRET','').strip()
RAZORPAY_WEBHOOK_SECRET=os.environ.get('RAZORPAY_WEBHOOK_SECRET','').strip()
app=Flask(__name__); app.secret_key=os.environ.get('EVENTORA_SECRET_KEY','eventora-local-change-me'); app.config['MAX_CONTENT_LENGTH']=100*1024*1024; app.config['PERMANENT_SESSION_LIFETIME']=timedelta(days=7); app.config['SESSION_COOKIE_HTTPONLY']=True; app.config['SESSION_COOKIE_SAMESITE']='Lax'; UPLOADS.mkdir(parents=True,exist_ok=True)
def db(): c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c
def init_db():
 DB.parent.mkdir(parents=True,exist_ok=True); c=db()
 c.executescript("""CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT,uid TEXT UNIQUE NOT NULL,name TEXT NOT NULL,email TEXT UNIQUE NOT NULL,phone TEXT DEFAULT '',password_hash TEXT NOT NULL,role TEXT DEFAULT 'user',photo TEXT DEFAULT '',dob TEXT DEFAULT '',address TEXT DEFAULT '',created_at TEXT DEFAULT CURRENT_TIMESTAMP);
 CREATE TABLE IF NOT EXISTS kv(path TEXT PRIMARY KEY,value TEXT);
 CREATE TABLE IF NOT EXISTS uploads(id INTEGER PRIMARY KEY AUTOINCREMENT,original_name TEXT,stored_name TEXT UNIQUE,path TEXT,mime_type TEXT,size INTEGER,uploaded_by TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
 CREATE TABLE IF NOT EXISTS reset_tokens(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,token_hash TEXT UNIQUE NOT NULL,expires_at TEXT NOT NULL,used INTEGER DEFAULT 0,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
 CREATE TABLE IF NOT EXISTS payment_attempts(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,event_id TEXT NOT NULL,event_title TEXT,amount REAL NOT NULL,razorpay_order_id TEXT UNIQUE NOT NULL,status TEXT DEFAULT 'created',razorpay_payment_id TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);""")
 if not c.execute('SELECT id FROM users WHERE email=?',(ADMIN_EMAIL,)).fetchone(): c.execute('INSERT INTO users(uid,name,email,password_hash,role) VALUES(?,?,?,?,?)',('admin_'+secrets.token_urlsafe(10),'Eventora Admin',ADMIN_EMAIL,generate_password_hash(ADMIN_PASSWORD),'admin'))
 else: c.execute("UPDATE users SET role='admin' WHERE email=?",(ADMIN_EMAIL,))
 if not c.execute('SELECT id FROM users WHERE email=?',(LEGACY_ADMIN_EMAIL,)).fetchone(): c.execute('INSERT INTO users(uid,name,email,password_hash,role) VALUES(?,?,?,?,?)',('legacy_admin_'+secrets.token_urlsafe(10),'Eventora Admin',LEGACY_ADMIN_EMAIL,generate_password_hash(ADMIN_PASSWORD),'admin'))
 else: c.execute("UPDATE users SET role='admin' WHERE email=?",(LEGACY_ADMIN_EMAIL,))
 # One-time migration: make the supplied demo admin credentials work with an older Eventora database.
 if not c.execute("SELECT 1 FROM kv WHERE path='system/admin_bootstrapped'").fetchone():
  c.execute("UPDATE users SET password_hash=?, role='admin' WHERE email IN (?,?)", (generate_password_hash(ADMIN_PASSWORD), ADMIN_EMAIL, LEGACY_ADMIN_EMAIL))
  c.execute("INSERT OR REPLACE INTO kv(path,value) VALUES('system/admin_bootstrapped','true')")
 c.commit();c.close()
def cp(p): return '/'.join(x for x in p.strip('/').split('/') if x)
def nested(o,ks,v):
 cur=o
 for k in ks[:-1]:
  if not isinstance(cur,dict): return
  cur=cur.setdefault(k,{})
 if ks and isinstance(cur,dict): cur[ks[-1]]=v
def getv(p):
 p=cp(p);c=db()
 if not p:
  rows=c.execute('SELECT path,value FROM kv').fetchall();c.close();t={}
  for r in rows:nested(t,r['path'].split('/'),json.loads(r['value']))
  return t
 r=c.execute('SELECT value FROM kv WHERE path=?',(p,)).fetchone()
 if r:c.close();return json.loads(r['value'])
 rows=c.execute('SELECT path,value FROM kv WHERE path LIKE ?',(p+'/%',)).fetchall();c.close()
 if not rows:return None
 t={};pre=p+'/'
 for r in rows:nested(t,r['path'][len(pre):].split('/'),json.loads(r['value']))
 return t
def putv(p,v,merge=False):
 p=cp(p); c=db()
 if merge:
  old=getv(p)
  if isinstance(old,dict) and isinstance(v,dict):
   old.update(v); v=old
 c.execute('DELETE FROM kv WHERE path=? OR path LIKE ?',(p,p+'/%'))
 c.execute('INSERT OR REPLACE INTO kv(path,value) VALUES(?,?)',(p,json.dumps(v,ensure_ascii=False)))
 # Keep the canonical users table synchronized with the user profile stored by the UI.
 parts=p.split('/')
 if len(parts)>=2 and parts[0]=='users' and isinstance(v,dict):
  uid=parts[1]
  fields={k:v.get(k) for k in ('name','email','phone','photo','dob','address') if k in v}
  if fields:
   allowed={'name','email','phone','photo','dob','address'}
   sets=[]; vals=[]
   for k,val in fields.items():
    if k in allowed:
     sets.append(k+'=?'); vals.append(val or '')
   if sets:
    vals.append(uid)
    c.execute('UPDATE users SET '+','.join(sets)+' WHERE uid=?', vals)
 c.commit(); c.close()

def delv(p):
 p=cp(p);c=db();c.execute('DELETE FROM kv WHERE path=? OR path LIKE ?',(p,p+'/%'));c.commit();c.close()
def pub(r):
 return None if not r else {'uid':r['uid'],'id':r['id'],'name':r['name'],'email':r['email'],'phone':r['phone'],'photo':r['photo'],'dob':r['dob'],'address':r['address'],'role':r['role']}

def current_row():
    """Return the currently authenticated user row, or None."""
    uid=session.get('uid')
    if not uid:
        return None
    c=db()
    try:
        return c.execute('SELECT * FROM users WHERE uid=?',(uid,)).fetchone()
    finally:
        c.close()

def admin_required():
 uid=session.get('uid')
 if not uid: return None, (jsonify(message='Login required.',code='auth/required'),401)
 c=db(); r=c.execute('SELECT * FROM users WHERE uid=?',(uid,)).fetchone(); c.close()
 if not r or r['role']!='admin': return None, (jsonify(message='Admin access required.',code='auth/admin-only'),403)
 return r, None

def profile_complete(row):
    if not row:
        return False
    vals = [row['name'], row['phone'], row['dob'], row['address']]
    return all(str(v or '').strip() for v in vals)

def make_notification(user_email,text,kind='info'):
 nid=str(int(datetime.now().timestamp()*1000))+'_'+secrets.token_hex(3)
 putv('notifications/'+nid,{'id':nid,'userEmail':user_email,'text':text,'kind':kind,'date':datetime.now(timezone.utc).isoformat(),'read':False})
 return nid
@app.get('/')
def home(): return render_template('index.html')
@app.get('/api/health')
def health(): return jsonify(ok=True,app='Eventora',firebase=False,database='SQLite',razorpay_configured=bool(RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET))

@app.route('/api/admin/color', methods=['POST'])
def admin_color():
    r, err = admin_required()
    if err:
        return err
    d = request.get_json(silent=True) or {}
    color = str(d.get('color') or '').strip()
    text = str(d.get('textColor') or '#ffffff').strip()
    if not re.fullmatch(r'#[0-9a-fA-F]{6}', color):
        return jsonify(message='Invalid accent color.', code='validation/color'), 400
    if not re.fullmatch(r'#[0-9a-fA-F]{6}', text):
        return jsonify(message='Invalid accent text color.', code='validation/text-color'), 400

    # Save into the canonical website-content record.
    current = getv('content') or {}
    if not isinstance(current, dict):
        current = {}
    current['accent_color'] = color
    current['accent_text_color'] = text
    putv('content', current, False)
    return jsonify(ok=True, color=color, textColor=text)

@app.route('/api/data/<path:p>',methods=['GET','PUT','PATCH','DELETE'])
def data(p):
    p = cp(p)
    parts = p.split('/') if p else []
    row = current_row()
    is_admin = bool(row and row['role'] == 'admin')

    public_paths = {'events','content','stats'}
    protected_roots = {'users','registrations','payments','wishlist','notifications','messages','auditLogs','adminProfile'}

    def email_of(obj):
        if not isinstance(obj, dict):
            return ''
        return str(obj.get('userEmail') or obj.get('email') or '').strip().lower()

    def own_user_email():
        return str(row['email'] if row else '').strip().lower()

    def scoped_root(root):
        value = getv(root)
        if is_admin:
            return value
        if not row:
            return None
        wanted = own_user_email()
        if not isinstance(value, dict):
            return {}
        if root == 'users':
            return {row['uid']: getv('users/'+row['uid']) or pub(row)}
        if root in {'registrations','payments'}:
            return {k:v for k,v in value.items() if email_of(v) == wanted}
        if root == 'wishlist':
            return {k:v for k,v in value.items() if email_of(v) == wanted}
        if root == 'notifications':
            return {k:v for k,v in value.items() if str(v.get('userEmail') or '').strip().lower() == wanted}
        if root == 'messages':
            return {k:v for k,v in value.items()
                    if str(v.get('email') or '').strip().lower() == wanted
                    or str(v.get('toEmail') or '').strip().lower() == wanted
                    or str(v.get('userEmail') or '').strip().lower() == wanted}
        return value

    # GET: only public site data is public; private collections are scoped.
    if request.method == 'GET':
        if not parts:
            return jsonify(value=getv(p))
        root = parts[0]
        if root in public_paths:
            # Stats are computed live from the canonical SQLite database so the
            # public counter never depends on a stale client-side value.
            if root == 'stats' and len(parts) == 1:
                c = db()
                try:
                    event_count = c.execute("SELECT COUNT(*) FROM kv WHERE path LIKE 'events/%'").fetchone()[0]
                    user_count = c.execute("SELECT COUNT(*) FROM users WHERE role='user'").fetchone()[0]
                finally:
                    c.close()
                content = getv('content') or {}
                cats = content.get('categories') if isinstance(content, dict) else None
                category_count = len(cats) if isinstance(cats, list) else 0
                return jsonify(value={
                    'eventsHosted': event_count,
                    'registeredUsers': user_count,
                    'eventCategories': category_count
                })
            return jsonify(value=getv(p))
        if root not in protected_roots:
            return jsonify(message='Unknown data path.', code='data/not-found'),404
        if not row:
            return jsonify(message='Login required.', code='auth/required'),401

        if len(parts) == 1:
            return jsonify(value=scoped_root(root))

        # Specific private record.
        value = getv(p)
        if is_admin:
            return jsonify(value=value)
        wanted = own_user_email()
        if root == 'users':
            allowed = len(parts) >= 2 and parts[1] == row['uid']
        elif root in {'registrations','payments','wishlist'}:
            allowed = email_of(value) == wanted
        elif root == 'notifications':
            allowed = isinstance(value,dict) and str(value.get('userEmail') or '').strip().lower() == wanted
        elif root == 'messages':
            allowed = isinstance(value,dict) and (
                str(value.get('email') or '').strip().lower() == wanted
                or str(value.get('toEmail') or '').strip().lower() == wanted
                or str(value.get('userEmail') or '').strip().lower() == wanted
            )
        else:
            allowed = False
        if not allowed:
            return jsonify(message='You do not have permission to read this data.',code='auth/forbidden'),403
        return jsonify(value=value)

    # Every write/delete requires authentication except the one-time public event/content
    # bootstrap used by the frontend when the database is completely empty.
    if not row:
        if request.method in ('PUT','PATCH') and p.startswith('events/') and getv('events') is None:
            pass
        elif request.method in ('PUT','PATCH') and p == 'content' and getv('content') is None:
            pass
        else:
            return jsonify(message='Login required for changes.',code='auth/required'),401

    if is_admin:
        allowed = True
    else:
        allowed = False
        root = parts[0] if parts else ''
        if root == 'users' and len(parts) >= 2 and parts[1] == row['uid']:
            allowed = True
        elif root in {'wishlist','notifications','messages'} and len(parts) >= 2:
            existing = getv(p)
            wanted = own_user_email()
            if request.method == 'DELETE':
                if root == 'wishlist':
                    allowed = email_of(existing) == wanted
                elif root == 'notifications':
                    allowed = isinstance(existing,dict) and str(existing.get('userEmail') or '').strip().lower() == wanted
                elif root == 'messages':
                    allowed = isinstance(existing,dict) and (
                        str(existing.get('email') or '').strip().lower() == wanted
                        or str(existing.get('toEmail') or '').strip().lower() == wanted
                        or str(existing.get('userEmail') or '').strip().lower() == wanted
                    )
            else:
                d=request.get_json(silent=True) or {}
                v=d.get('value') or {}
                if not isinstance(v,dict):
                    return jsonify(message='Invalid data payload.',code='validation/data'),400
                if root == 'wishlist':
                    allowed = str(v.get('userEmail') or '').strip().lower() == wanted
                elif root == 'notifications':
                    allowed = str(v.get('userEmail') or '').strip().lower() == wanted
                elif root == 'messages':
                    allowed = str(v.get('email') or '').strip().lower() == wanted
        # Registrations, payments, stats, audit logs and adminProfile are server/admin only.

    if not allowed:
        return jsonify(message='You do not have permission to modify this data.',code='auth/forbidden'),403

    if request.method == 'DELETE':
        delv(p)
        return jsonify(ok=True)

    d=request.get_json(silent=True) or {}
    value=d.get('value')
    # Profile edits are validated on the server as well, so a user cannot
    # bypass the mobile-number rule by editing the browser request.
    if parts and parts[0] == 'users' and len(parts) >= 2 and isinstance(value, dict):
        if any(k in value for k in ('name','phone','dob','address')):
            name=str(value.get('name','')).strip()
            phone=re.sub(r'[\s-]','',str(value.get('phone','')).strip())
            if phone.startswith('+91'): phone=phone[3:]
            if not re.fullmatch(r'[A-Za-z][A-Za-z .\'-]{1,49}', name):
                return jsonify(message='Please enter a valid full name.',code='validation/name'),400
            if not re.fullmatch(r'[6-9]\d{9}', phone):
                return jsonify(message='Please enter a valid 10-digit Indian mobile number.',code='validation/phone'),400
            if not str(value.get('dob','')).strip() or not str(value.get('address','')).strip():
                return jsonify(message='Date of birth and address are required.',code='validation/profile'),400
            value['phone']=phone
    if request.method == 'PATCH':
        if not isinstance(value,dict):
            return jsonify(message='PATCH value must be an object.',code='validation/data'),400
        putv(p,value,True)
    else:
        putv(p,value,False)
    return jsonify(ok=True,value=getv(p))

@app.post('/api/auth/register')
def register():
 d=request.get_json(silent=True) or {}
 e=(d.get('email') or '').strip().lower()
 pw=d.get('password') or ''
 name=(d.get('name') or '').strip()
 phone=(d.get('phone') or '').strip()

 # Server-side validation (cannot be bypassed by changing browser JavaScript).
 if not re.fullmatch(r"[A-Za-z][A-Za-z .'-]{1,49}", name):
  return jsonify(message='Full Name must contain only letters, spaces, apostrophes, dots or hyphens (2-50 characters).',code='validation/name'),400
 if not re.fullmatch(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$", e):
  return jsonify(message='Please enter a valid email address.',code='validation/email'),400
 phone_normalized=re.sub(r'[\s-]', '', phone)
 if phone_normalized.startswith('+91'):
  phone_normalized=phone_normalized[3:]
 if not re.fullmatch(r'[6-9]\d{9}', phone_normalized):
  return jsonify(message='Please enter a valid 10-digit Indian mobile number starting with 6, 7, 8 or 9.',code='validation/phone'),400
 phone=phone_normalized
 if len(pw)<8 or not re.search(r"[A-Z]",pw) or not re.search(r"[a-z]",pw) or not re.search(r"\d",pw) or not re.search(r"[^A-Za-z0-9]",pw):
  return jsonify(message='Password must be 8+ characters and include uppercase, lowercase, number and special character.',code='validation/password'),400

 c=db()
 if c.execute('SELECT 1 FROM users WHERE email=?',(e,)).fetchone():
  c.close()
  return jsonify(message='Email already in use. Please use another email.',code='auth/email-already-in-use'),409
 if c.execute('SELECT 1 FROM users WHERE phone=?',(phone,)).fetchone():
  c.close()
  return jsonify(message='This mobile number is already registered. Please use another number.',code='auth/phone-already-in-use'),409
 uid='ev_'+secrets.token_urlsafe(12)
 # Public sign-up can NEVER create an admin account. Admin accounts are bootstrapped
 # only by the server during initialization and can only be reached with admin credentials.
 role='user'
 c.execute('INSERT INTO users(uid,name,email,phone,password_hash,role) VALUES(?,?,?,?,?,?)',
           (uid,name,e,phone,generate_password_hash(pw),role))
 c.commit()
 r=c.execute('SELECT * FROM users WHERE uid=?',(uid,)).fetchone()
 c.close()
 session.permanent=True
 session['uid']=uid
 return jsonify(user=pub(r))

@app.post('/api/auth/login')
def login():
 d=request.get_json(silent=True) or {};e=(d.get('email') or '').strip().lower();pw=d.get('password') or '';c=db();r=c.execute('SELECT * FROM users WHERE email=?',(e,)).fetchone();c.close()
 if not r or not check_password_hash(r['password_hash'],pw):return jsonify(message='Invalid email or password.',code='auth/invalid-credential'),401
 session.permanent=True;session['uid']=r['uid'];return jsonify(user=pub(r))
@app.post('/api/auth/logout')
def logout():session.clear();return jsonify(ok=True)
@app.get('/api/auth/me')
def me():
 uid=session.get('uid')
 if not uid:return jsonify(user=None)
 c=db();r=c.execute('SELECT * FROM users WHERE uid=?',(uid,)).fetchone();c.close();return jsonify(user=pub(r))
@app.get('/api/admin/users')
def admin_users():
 uid=session.get('uid')
 if not uid:
  return jsonify(message='Login required.',code='auth/required'),401
 c=db()
 me=c.execute('SELECT role FROM users WHERE uid=?',(uid,)).fetchone()
 if not me or me['role']!='admin':
  c.close()
  return jsonify(message='Admin access required.',code='auth/admin-only'),403
 rows=c.execute('SELECT id,uid,name,email,phone,role,photo,dob,address,created_at FROM users ORDER BY id DESC').fetchall()
 c.close()
 users=[dict(r) for r in rows]
 # Normalize fields used by the Admin Participants details panel.
 for u in users:
  u['databaseId']=u.get('id')
  u['createdAt']=u.get('created_at')
  u['emailNormalized']=str(u.get('email') or '').strip().lower()
 # Merge any extra profile fields kept by the UI's data store.
 for u in users:
  extra=getv('users/'+u['uid'])
  if isinstance(extra,dict):
   for k in ('name','email','phone','photo','dob','address'):
    if extra.get(k) is not None:
     u[k]=extra.get(k)
 return jsonify(users=users)

@app.post('/api/admin/users/<uid>/change-password')
def admin_change_user_password(uid):
    """Allow an authenticated admin to set a user's password without exposing the old password."""
    admin_uid=session.get('uid')
    if not admin_uid:
        return jsonify(message='Login required.', code='auth/required'),401
    d=request.get_json(silent=True) or {}
    new=(d.get('new_password') or '').strip()
    confirm=(d.get('confirm_password') or '').strip()
    if new != confirm:
        return jsonify(message='New password and confirmation do not match.', code='validation/password-match'),400
    if len(new)<8 or not re.search(r'[A-Z]',new) or not re.search(r'[a-z]',new) or not re.search(r'\d',new) or not re.search(r'[^A-Za-z0-9]',new):
        return jsonify(message='Password must be 8+ characters and include uppercase, lowercase, number and special character.', code='validation/password'),400
    c=db()
    admin=c.execute('SELECT role FROM users WHERE uid=?',(admin_uid,)).fetchone()
    if not admin or admin['role']!='admin':
        c.close(); return jsonify(message='Admin access required.', code='auth/admin-only'),403
    user=c.execute('SELECT id,uid,name,email,role FROM users WHERE uid=?',(uid,)).fetchone()
    if not user:
        c.close(); return jsonify(message='User not found.', code='user/not-found'),404
    c.execute('UPDATE users SET password_hash=? WHERE uid=?',(generate_password_hash(new),uid))
    c.commit(); c.close()
    return jsonify(ok=True, user={'uid':user['uid'],'name':user['name'],'email':user['email']})


@app.route('/api/profile', methods=['GET','PUT','PATCH'])
def profile_api():
    """Canonical user profile API backed directly by SQLite.
    This avoids relying on the legacy client data store for profile persistence.
    """
    uid=session.get('uid')
    if not uid:
        return jsonify(message='Login required.', code='auth/required'),401
    c=db()
    row=c.execute('SELECT * FROM users WHERE uid=?',(uid,)).fetchone()
    if not row:
        c.close()
        session.clear()
        return jsonify(message='User account not found.', code='auth/user-not-found'),404
    if request.method=='GET':
        out=pub(row)
        c.close()
        return jsonify(user=out)
    d=request.get_json(silent=True) or {}
    name=str(d.get('name', row['name']) or '').strip()
    phone=re.sub(r'[\s-]','',str(d.get('phone', row['phone']) or '').strip())
    if phone.startswith('+91'):
        phone=phone[3:]
    dob=str(d.get('dob', row['dob']) or '').strip()
    address=str(d.get('address', row['address']) or '').strip()
    photo=d.get('photo', row['photo']) or ''
    if not re.fullmatch(r"[A-Za-z][A-Za-z .'-]{1,49}", name):
        c.close(); return jsonify(message='Please enter a valid full name.',code='validation/name'),400
    if not re.fullmatch(r'[6-9]\d{9}', phone):
        c.close(); return jsonify(message='Please enter a valid 10-digit Indian mobile number.',code='validation/phone'),400
    if not dob or not address:
        c.close(); return jsonify(message='Date of birth and address are required.',code='validation/profile'),400
    other=c.execute('SELECT id FROM users WHERE phone=? AND uid<>?',(phone,uid)).fetchone()
    if other:
        c.close(); return jsonify(message='This mobile number is already registered to another account.',code='auth/phone-already-in-use'),409
    # Keep photo bounded; the UI sends a data URL. Reject malformed/huge values.
    if photo and (not isinstance(photo,str) or len(photo)>8*1024*1024):
        c.close(); return jsonify(message='Profile photo is too large.',code='validation/photo'),400
    c.execute('UPDATE users SET name=?,phone=?,dob=?,address=?,photo=? WHERE uid=?',(name,phone,dob,address,photo,uid))
    c.commit()
    updated=c.execute('SELECT * FROM users WHERE uid=?',(uid,)).fetchone()
    c.close()
    # Mirror to the legacy data store for compatibility with older UI features.
    putv('users/'+uid, {'uid':uid,'name':name,'email':updated['email'],'phone':phone,'dob':dob,'address':address,'photo':photo}, False)
    return jsonify(ok=True,user=pub(updated))

@app.post('/api/auth/change-password')
def change_password():
    uid=session.get('uid')
    if not uid: return jsonify(message='Login required.',code='auth/required'),401
    d=request.get_json(silent=True) or {}; old=d.get('old_password') or ''; new=d.get('new_password') or ''; confirm=d.get('confirm_password') or ''
    if confirm and new != confirm: return jsonify(message='New password and confirmation do not match.',code='validation/password-match'),400
    c=db(); r=c.execute('SELECT * FROM users WHERE uid=?',(uid,)).fetchone()
    if not r or not check_password_hash(r['password_hash'],old): c.close(); return jsonify(message='Current password is incorrect.',code='auth/wrong-password'),400
    if len(new)<8 or not re.search(r'[A-Z]',new) or not re.search(r'[a-z]',new) or not re.search(r'\d',new) or not re.search(r'[^A-Za-z0-9]',new): c.close(); return jsonify(message='New password must be strong: 8+ characters, uppercase, lowercase, number and special character.',code='validation/password'),400
    c.execute('UPDATE users SET password_hash=? WHERE uid=?',(generate_password_hash(new),uid)); c.commit(); c.close(); return jsonify(ok=True)

@app.post('/api/auth/forgot')
def forgot_password():
    d=request.get_json(silent=True) or {}; email=(d.get('email') or '').strip().lower(); c=db(); r=c.execute('SELECT * FROM users WHERE email=?',(email,)).fetchone()
    if not r: c.close(); return jsonify(ok=True,message='If the account exists, a reset option has been generated.')
    token=secrets.token_urlsafe(32); token_hash=hashlib.sha256(token.encode()).hexdigest(); exp=(datetime.now(timezone.utc)+timedelta(minutes=30)).isoformat()
    c.execute('UPDATE reset_tokens SET used=1 WHERE user_id=? AND used=0',(r['id'],)); c.execute('INSERT INTO reset_tokens(user_id,token_hash,expires_at) VALUES(?,?,?)',(r['id'],token_hash,exp)); c.commit(); c.close()
    # Local fallback returns a one-time token so the feature works without SMTP. Configure SMTP for real email delivery.
    return jsonify(ok=True,message='Reset link generated.',reset_token=token)

@app.post('/api/auth/reset')
def reset_password():
    d=request.get_json(silent=True) or {}; token=d.get('token') or ''; new=d.get('password') or ''
    if len(new)<8 or not re.search(r'[A-Z]',new) or not re.search(r'[a-z]',new) or not re.search(r'\d',new) or not re.search(r'[^A-Za-z0-9]',new): return jsonify(message='Password must be strong: 8+ characters, uppercase, lowercase, number and special character.'),400
    th=hashlib.sha256(token.encode()).hexdigest(); c=db(); row=c.execute('SELECT * FROM reset_tokens WHERE token_hash=? AND used=0 AND expires_at>?',(th,datetime.now(timezone.utc).isoformat())).fetchone()
    if not row: c.close(); return jsonify(message='Reset link is invalid or expired.'),400
    c.execute('UPDATE users SET password_hash=? WHERE id=?',(generate_password_hash(new),row['user_id'])); c.execute('UPDATE reset_tokens SET used=1 WHERE id=?',(row['id'],)); c.commit(); c.close(); return jsonify(ok=True)

@app.post('/api/registrations/free')
def free_registration():
    uid=session.get('uid')
    if not uid: return jsonify(message='Please login first.',code='auth/required'),401
    d=request.get_json(silent=True) or {}; event_id=str(d.get('event_id')); events=getv('events') or {}; ev=events.get(event_id)
    if not isinstance(ev,dict): return jsonify(message='Event not found.'),404
    c=db(); user=c.execute('SELECT * FROM users WHERE uid=?',(uid,)).fetchone(); c.close()
    if not profile_complete(user):
        return jsonify(message='Complete your profile (Full Name, Phone, Date of Birth and Address) before registering for an event.',code='profile/incomplete'),400
    regs=getv('registrations') or {}
    if any(isinstance(x,dict) and str(x.get('eventId'))==event_id and x.get('userEmail')==user['email'] for x in regs.values()): return jsonify(message='Already registered.'),409
    if float(ev.get('price') or 0)>0: return jsonify(message='This event requires payment.'),400
    seats=int(ev.get('seats') or 0); registered=int(ev.get('registered') or 0)
    if seats and registered>=seats: return jsonify(message='No seats available.'),400
    rid=str(int(datetime.now().timestamp()*1000)); ticket='EV-'+event_id+'-'+rid
    reg={'id':rid,'ticketId':ticket,'eventId':event_id,'eventTitle':ev.get('title',''),'eventImage':ev.get('image',''),'userEmail':user['email'],'userName':user['name'],'userPhone':user['phone'],'userDob':user['dob'],'userAddress':user['address'],'userPhoto':user['photo'],'date':datetime.now(timezone.utc).isoformat(),'status':'pending','paymentId':None,'amountPaid':0}
    dbv=reg; putv('registrations/'+rid,dbv); make_notification(user['email'], f'Registration request received for {ev.get("title","event")}. Please wait for admin approval.', 'registration_pending'); return jsonify(ok=True,registration=reg)

@app.post('/api/registrations/payment-link')
def payment_link_registration():
    """Create a pending registration for an admin-supplied Razorpay Payment Link."""
    uid=session.get('uid')
    if not uid: return jsonify(message='Please login first.',code='auth/required'),401
    d=request.get_json(silent=True) or {}; event_id=str(d.get('event_id') or '')
    events=getv('events') or {}; ev=events.get(event_id)
    if not isinstance(ev,dict): return jsonify(message='Event not found.'),404
    payment_link=str(ev.get('paymentLink') or '').strip()
    if not payment_link: return jsonify(message='This event has no Razorpay Payment Link configured by admin.'),400
    parsed=urlparse(payment_link)
    if parsed.scheme not in ('http','https') or not parsed.netloc: return jsonify(message='Event Razorpay Payment Link is invalid.'),400
    if float(ev.get('price') or 0)<=0: return jsonify(message='This event is free.'),400
    c=db(); user=c.execute('SELECT * FROM users WHERE uid=?',(uid,)).fetchone(); c.close()
    if not user: return jsonify(message='User not found.'),404
    regs=getv('registrations') or {}
    if any(isinstance(x,dict) and str(x.get('eventId'))==event_id and x.get('userEmail','').lower()==user['email'].lower() for x in regs.values()):
        return jsonify(message='Already registered.'),409
    seats=int(ev.get('seats') or 0); registered=int(ev.get('registered') or 0)
    if seats and registered>=seats: return jsonify(message='No seats available.'),400
    rid=str(int(datetime.now().timestamp()*1000))
    ticket='EV-'+event_id+'-'+rid
    date=datetime.now(timezone.utc).isoformat()
    reg={'id':rid,'ticketId':ticket,'eventId':event_id,'eventTitle':ev.get('title',''),'eventImage':ev.get('image',''),
         'userEmail':user['email'],'userName':user['name'],'userPhone':user['phone'],'userDob':user['dob'],'userAddress':user['address'],
         'userPhoto':user['photo'],'date':date,'status':'pending','paymentId':None,'amountPaid':0,
         'eventAmount':float(ev.get('price') or 0),'paymentMethod':'Razorpay Payment Link','paymentLink':payment_link,'paymentStatus':'awaiting_payment'}
    putv('registrations/'+rid,reg)
    make_notification(user['email'], f'Registration request created for {ev.get("title","event")}. Complete the Razorpay payment link, then confirm that you have paid. Admin will verify within 12 hours.', 'registration_pending')
    return jsonify(ok=True,registration=reg,paymentLink=payment_link)

@app.post('/api/registrations/payment-link/paid')
def payment_link_paid():
    """Record that the user says they completed the external Razorpay Payment Link.
    Razorpay Payment Links are verified by the admin; this endpoint never marks the
    registration approved or fabricates a payment id."""
    uid=session.get('uid')
    if not uid: return jsonify(message='Please login first.',code='auth/required'),401
    d=request.get_json(silent=True) or {}; reg_id=str(d.get('registration_id') or '')
    if not reg_id: return jsonify(message='Registration ID is required.'),400
    reg=getv('registrations/'+reg_id)
    if not isinstance(reg,dict): return jsonify(message='Registration not found.'),404
    c=db(); user=c.execute('SELECT * FROM users WHERE uid=?',(uid,)).fetchone(); c.close()
    if not user or str(reg.get('userEmail','')).lower()!=str(user['email']).lower():
        return jsonify(message='You do not have permission to update this registration.',code='auth/forbidden'),403
    if str(reg.get('status','')).lower() in {'approved','rejected'}:
        return jsonify(message='This registration has already been finalized.',registration=reg),409
    amount=float(reg.get('eventAmount') or reg.get('amountPaid') or 0)
    if not amount:
        ev=getv('events/'+str(reg.get('eventId'))) or {}
        amount=float(ev.get('price') or 0)
    reg['amountPaid']=amount
    reg['paymentStatus']='user_claimed_paid'
    reg['paymentClaimedAt']=datetime.now(timezone.utc).isoformat()
    reg['paymentMethod']='Razorpay Payment Link'
    putv('registrations/'+reg_id,reg)
    # Keep a payment record for admin reporting, but clearly mark it as awaiting verification.
    putv('payments/'+reg_id,{'id':reg_id,'userEmail':user['email'],'userName':user['name'],'eventId':str(reg.get('eventId')),'eventTitle':reg.get('eventTitle',''),'amount':amount,'paymentId':None,'date':reg['paymentClaimedAt'],'status':'pending_verification','paymentMethod':'Razorpay Payment Link'})
    make_notification(ADMIN_EMAIL, f'💳 Payment claimed by {user["name"]} for {reg.get("eventTitle","event")}. Verify Razorpay and approve/reject the registration.', 'payment_claimed')
    make_notification(user['email'], f'Payment details submitted for {reg.get("eventTitle","event")}. Registration is pending admin verification for up to 12 hours.', 'payment_pending')
    return jsonify(ok=True,registration=reg,payment= getv('payments/'+reg_id))

@app.post('/api/payments/order')
def payment_order():
    uid=session.get('uid')
    if not uid: return jsonify(message='Please login first.',code='auth/required'),401
    if not (RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET and razorpay): return jsonify(message='Razorpay is not configured. Add RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET to .env, then restart Eventora.',code='payment/not-configured'),503
    d=request.get_json(silent=True) or {}; event_id=str(d.get('event_id')); events=getv('events') or {}; ev=events.get(event_id)
    if not isinstance(ev,dict): return jsonify(message='Event not found.'),404
    amount=float(ev.get('price') or 0)
    if amount<=0:return jsonify(message='This event is free.'),400
    c=db(); user=c.execute('SELECT * FROM users WHERE uid=?',(uid,)).fetchone(); c.close()
    if not profile_complete(user):
        return jsonify(message='Complete your profile (Full Name, Phone, Date of Birth and Address) before registering for an event.',code='profile/incomplete'),400
    regs=getv('registrations') or {}
    if any(isinstance(x,dict) and str(x.get('eventId'))==event_id and x.get('userEmail')==user['email'] for x in regs.values()): return jsonify(message='Already registered.'),409
    client=razorpay.Client(auth=(RAZORPAY_KEY_ID,RAZORPAY_KEY_SECRET)); order=client.order.create({'amount':int(round(amount*100)),'currency':'INR','receipt':'EV'+secrets.token_hex(8),'notes':{'event_id':event_id,'user_id':user['uid']},'capture':'automatic'})
    c=db(); c.execute('INSERT INTO payment_attempts(user_id,event_id,event_title,amount,razorpay_order_id) VALUES(?,?,?,?,?)',(user['id'],event_id,ev.get('title',''),amount,order['id'])); c.commit(); c.close()
    return jsonify(ok=True,key_id=RAZORPAY_KEY_ID,order_id=order['id'],amount=int(round(amount*100)),currency='INR',name=user['name'],email=user['email'],phone=user['phone'],event_id=event_id)

@app.post('/api/payments/verify')
def payment_verify():
    uid=session.get('uid')
    if not uid: return jsonify(message='Login required.',code='auth/required'),401
    d=request.get_json(silent=True) or {}; order_id=d.get('razorpay_order_id'); payment_id=d.get('razorpay_payment_id'); signature=d.get('razorpay_signature')
    if not all([order_id,payment_id,signature]): return jsonify(message='Incomplete Razorpay response.'),400
    c=db(); attempt=c.execute('SELECT * FROM payment_attempts WHERE razorpay_order_id=?',(order_id,)).fetchone(); user=c.execute('SELECT * FROM users WHERE uid=?',(uid,)).fetchone(); c.close()
    if not attempt or not user or attempt['user_id']!=user['id']: return jsonify(message='Payment order is invalid.'),403
    expected=hmac.new(RAZORPAY_KEY_SECRET.encode(),f'{order_id}|{payment_id}'.encode(),hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected,signature): return jsonify(message='Payment signature verification failed.'),400
    events=getv('events') or {}; ev=events.get(str(attempt['event_id']))
    if not isinstance(ev,dict): return jsonify(message='Event not found.'),404
    rid=str(int(datetime.now().timestamp()*1000)); ticket='EV-'+str(attempt['event_id'])+'-'+rid; date=datetime.now(timezone.utc).isoformat()
    reg={'id':rid,'ticketId':ticket,'eventId':str(attempt['event_id']),'eventTitle':attempt['event_title'],'eventImage':ev.get('image',''),'userEmail':user['email'],'userName':user['name'],'userPhone':user['phone'],'userDob':user['dob'],'userAddress':user['address'],'userPhoto':user['photo'],'date':date,'status':'pending','paymentId':payment_id,'razorpayOrderId':order_id,'amountPaid':float(attempt['amount'])}
    pay={'id':rid,'userEmail':user['email'],'userName':user['name'],'eventId':str(attempt['event_id']),'eventTitle':attempt['event_title'],'amount':float(attempt['amount']),'paymentId':payment_id,'razorpayOrderId':order_id,'date':date,'status':'pending'}
    putv('registrations/'+rid,reg); putv('payments/'+rid,pay); make_notification(user['email'], f'Payment received for {attempt["event_title"]}. Your registration is pending admin approval.', 'payment_pending')
    c=db(); c.execute('UPDATE payment_attempts SET status=?,razorpay_payment_id=? WHERE id=?',('pending',payment_id,attempt['id'])); c.commit(); c.close(); return jsonify(ok=True,registration=reg,payment=pay)

@app.post('/api/razorpay/webhook')
def razorpay_webhook():
    if not RAZORPAY_WEBHOOK_SECRET:return jsonify(message='Webhook secret not configured.'),503
    raw=request.get_data(); sig=request.headers.get('X-Razorpay-Signature',''); expected=hmac.new(RAZORPAY_WEBHOOK_SECRET.encode(),raw,hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected,sig):return jsonify(message='Invalid webhook signature.'),400
    return jsonify(ok=True)

@app.post('/api/admin/events')
def admin_create_event():
 admin,err=admin_required()
 if err:return err
 d=request.get_json(silent=True) or {}; event=d.get('event') or d
 title=str(d.get('title') or event.get('title') or '').strip()
 date=str(d.get('date') or event.get('date') or '').strip(); venue=str(d.get('venue') or event.get('venue') or '').strip()
 if not title or not date or not venue:return jsonify(message='Title, date and venue are required.'),400
 payment_link=str(event.get('paymentLink') or '').strip()
 price=float(event.get('price') or 0)
 if price>0 and not payment_link:
  return jsonify(message='For a paid event, add the Razorpay Payment Link before saving the event.'),400
 if payment_link:
  parsed=urlparse(payment_link)
  if parsed.scheme not in ('http','https') or not parsed.netloc:
   return jsonify(message='Razorpay Payment Link must be a valid http/https URL (example: https://rzp.io/rzp/...).'),400
 event={**event,'title':title,'date':date,'venue':venue,'paymentLink':payment_link,'id':str(event.get('id') or int(datetime.now().timestamp()*1000)),'registered':int(event.get('registered') or 0)}
 putv('events/'+event['id'],event); return jsonify(ok=True,event=event)

@app.patch('/api/admin/events/<event_id>')
def admin_update_event(event_id):
 admin,err=admin_required()
 if err:return err
 d=request.get_json(silent=True) or {}; existing=getv('events/'+str(event_id)) or {}
 if not isinstance(existing,dict):return jsonify(message='Event not found.'),404
 existing.update(d.get('event') or d); existing['id']=str(event_id)
 payment_link=str(existing.get('paymentLink') or '').strip()
 price=float(existing.get('price') or 0)
 if price>0 and not payment_link:
  return jsonify(message='For a paid event, add the Razorpay Payment Link before saving the event.'),400
 if payment_link:
  parsed=urlparse(payment_link)
  if parsed.scheme not in ('http','https') or not parsed.netloc:
   return jsonify(message='Razorpay Payment Link must be a valid http/https URL (example: https://rzp.io/rzp/...).'),400
 existing['paymentLink']=payment_link
 putv('events/'+str(event_id),existing); return jsonify(ok=True,event=existing)

@app.delete('/api/admin/events/<event_id>')
def admin_delete_event(event_id):
 admin,err=admin_required()
 if err:return err
 if getv('events/'+str(event_id)) is None:return jsonify(message='Event not found.'),404
 delv('events/'+str(event_id)); return jsonify(ok=True)

@app.post('/api/admin/registrations/<reg_id>/approve')
def admin_approve_registration(reg_id):
 admin,err=admin_required()
 if err:return err
 reg=getv('registrations/'+str(reg_id))
 if not isinstance(reg,dict):return jsonify(message='Registration not found.'),404
 if reg.get('status')=='approved':return jsonify(ok=True,registration=reg,message='Already approved.')
 ev=getv('events/'+str(reg.get('eventId'))) or {}
 seats=int(ev.get('seats') or 0); registered=int(ev.get('registered') or 0)
 if seats and registered>=seats:return jsonify(message='No seats available for this event.'),409
 reg['status']='approved'; reg['approvedAt']=datetime.now(timezone.utc).isoformat(); reg['approvedBy']=admin['email']
 putv('registrations/'+str(reg_id),reg)
 if reg.get('amountPaid',0)>0:
  pay=getv('payments/'+str(reg_id)) or {}; pay.update({'status':'success','approvedAt':reg['approvedAt'],'approvedBy':admin['email']}); putv('payments/'+str(reg_id),pay)
 ev['registered']=registered+1; putv('events/'+str(reg.get('eventId')),ev)
 make_notification(reg.get('userEmail',''),f'🎉 Registration approved for {reg.get("eventTitle","your event")}. Your QR ticket and WhatsApp ticket are now available in My Events.','registration_approved')
 logid=str(int(datetime.now().timestamp()*1000))+'_'+secrets.token_hex(3)
 putv('auditLogs/'+logid,{'id':logid,'registrationId':str(reg_id),'adminEmail':admin['email'],'action':'Approved','participantName':reg.get('userName','N/A'),'participantEmail':reg.get('userEmail','N/A'),'eventTitle':reg.get('eventTitle','N/A'),'date':datetime.now(timezone.utc).isoformat()})
 return jsonify(ok=True,registration=reg)

@app.post('/api/admin/registrations/<reg_id>/reject')
def admin_reject_registration(reg_id):
 admin,err=admin_required()
 if err:return err
 reg=getv('registrations/'+str(reg_id))
 if not isinstance(reg,dict):return jsonify(message='Registration not found.'),404
 reg['status']='rejected'; reg['rejectedAt']=datetime.now(timezone.utc).isoformat(); reg['rejectedBy']=admin['email']; putv('registrations/'+str(reg_id),reg)
 if reg.get('amountPaid',0)>0:
  pay=getv('payments/'+str(reg_id)) or {}; pay.update({'status':'rejected','rejectedAt':reg['rejectedAt'],'rejectedBy':admin['email']}); putv('payments/'+str(reg_id),pay)
 make_notification(reg.get('userEmail',''),f'Your registration request for {reg.get("eventTitle","the event")} was rejected by admin.','registration_rejected')
 logid=str(int(datetime.now().timestamp()*1000))+'_'+secrets.token_hex(3)
 putv('auditLogs/'+logid,{'id':logid,'registrationId':str(reg_id),'adminEmail':admin['email'],'action':'Rejected','participantName':reg.get('userName','N/A'),'participantEmail':reg.get('userEmail','N/A'),'eventTitle':reg.get('eventTitle','N/A'),'date':datetime.now(timezone.utc).isoformat()})
 return jsonify(ok=True,registration=reg)

@app.post('/api/admin/message')
def admin_message_user():
 admin,err=admin_required()
 if err:return err
 d=request.get_json(silent=True) or {}; email=str(d.get('email') or '').strip().lower(); text=str(d.get('message') or '').strip()
 if not email or not text:return jsonify(message='User email and message are required.'),400
 c=db(); user=c.execute('SELECT * FROM users WHERE lower(email)=?',(email,)).fetchone(); c.close()
 if not user:return jsonify(message='User not found.'),404
 make_notification(email,f'📩 Admin message: {text}','admin_message')
 mid=str(int(datetime.now().timestamp()*1000))+'_'+secrets.token_hex(3)
 putv('messages/'+mid,{'id':mid,'name':admin['name'],'email':admin['email'],'message':text,'toEmail':email,'type':'admin_to_user','date':datetime.now(timezone.utc).isoformat(),'read':False})
 return jsonify(ok=True,message='Message sent to user.')

@app.post('/api/upload')
def upload():
 f=request.files.get('file')
 if not f:return jsonify(message='No file supplied.'),400
 original=secure_filename(f.filename or 'upload');ext=Path(original).suffix.lower()
 if ext not in {'.jpg','.jpeg','.png','.gif','.webp','.svg','.mp4','.webm','.mov','.pdf'}:return jsonify(message='File type not allowed.'),400
 stored=uuid.uuid4().hex+ext;dest=UPLOADS/stored;f.save(dest);url='/static/uploads/'+stored;c=db();c.execute('INSERT INTO uploads(original_name,stored_name,path,mime_type,size,uploaded_by) VALUES(?,?,?,?,?,?)',(original,stored,url,f.mimetype or '',dest.stat().st_size,session.get('uid','guest')));c.commit();c.close();return jsonify(ok=True,url=url,filename=original)
@app.get('/api/uploads')
def uploads():
 c=db();rows=c.execute('SELECT * FROM uploads ORDER BY id DESC').fetchall();c.close();return jsonify([dict(r) for r in rows])
# Initialize SQLite schema when the process starts (works with Gunicorn/Railway too).
init_db()

if __name__=='__main__':
    port=int(os.environ.get('PORT','5000'))
    app.run(host='0.0.0.0',port=port,debug=False)

