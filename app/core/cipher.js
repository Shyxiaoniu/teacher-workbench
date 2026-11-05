/* ============================================================
   数据加密核心（AES-256-CBC + HMAC-SHA256，密钥 PBKDF2 派生）
   ------------------------------------------------------------
   · 唯一实现，两处共用：教师端 index.html（<script src="/core/cipher.js">）、
     登录页「数据导入」（同一地址）。抽出成文件是 2026-11-05 的事：
     登录页要在未登录时解开 .cwbackup 全量备份，解密逻辑必须与导出端
     逐字节一致，复制一份迟早漂移。
   · 纯 JS 实现（已用 Node 官方 crypto 严格对照验证），全浏览器可用，
     不依赖 WebCrypto（老 Edge / file:// 场景 crypto.subtle 不可靠）。
   · 存储格式：v1:c:<iv>:<mac>:<ct>（base64）。解密时先校验 HMAC 防篡改。
   · 导出端（backup.js encWithKey / index.html encStr）与这里保持同格式。
   ============================================================ */
(function (root) {
  'use strict';
  const SHA_K = [
    0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
    0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
    0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
    0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
    0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
    0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
    0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
    0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2 ];
  function rotr(x, n) { return (x >>> n) | (x << (32 - n)); }
  function sha256(msg) {
    let h0=0x6a09e667,h1=0xbb67ae85,h2=0x3c6ef372,h3=0xa54ff53a,h4=0x510e527f,h5=0x9b05688c,h6=0x1f83d9ab,h7=0x5be0cd19;
    const l = msg.length, bitLen = l * 8;
    const total = ((l + 1 + 8 + 63) & ~63);
    const m = new Uint8Array(total); m.set(msg); m[l] = 0x80;
    const dv = new DataView(m.buffer);
    dv.setUint32(total - 8, Math.floor(bitLen / 0x100000000));
    dv.setUint32(total - 4, bitLen >>> 0);
    for (let off = 0; off < total; off += 64) {
      const w = new Uint32Array(64);
      for (let i = 0; i < 16; i++) w[i] = dv.getUint32(off + i * 4);
      for (let i = 16; i < 64; i++) {
        const s0 = rotr(w[i-15],7) ^ rotr(w[i-15],18) ^ (w[i-15] >>> 3);
        const s1 = rotr(w[i-2],17) ^ rotr(w[i-2],19) ^ (w[i-2] >>> 10);
        w[i] = (w[i-16] + s0 + w[i-7] + s1) >>> 0;
      }
      let a=h0,b=h1,c=h2,d=h3,e=h4,f=h5,g=h6,h=h7;
      for (let i = 0; i < 64; i++) {
        const S1 = rotr(e,6) ^ rotr(e,11) ^ rotr(e,25);
        const ch = (e & f) ^ ((~e) & g);
        const t1 = (h + S1 + ch + SHA_K[i] + w[i]) >>> 0;
        const S0 = rotr(a,2) ^ rotr(a,13) ^ rotr(a,22);
        const maj = (a & b) ^ (a & c) ^ (b & c);
        const t2 = (S0 + maj) >>> 0;
        h=g;g=f;f=e;e=(d+t1)>>>0;d=c;c=b;b=a;a=(t1+t2)>>>0;
      }
      h0=(h0+a)>>>0;h1=(h1+b)>>>0;h2=(h2+c)>>>0;h3=(h3+d)>>>0;h4=(h4+e)>>>0;h5=(h5+f)>>>0;h6=(h6+g)>>>0;h7=(h7+h)>>>0;
    }
    const out = new Uint8Array(32); const odv = new DataView(out.buffer);
    odv.setUint32(0,h0);odv.setUint32(4,h1);odv.setUint32(8,h2);odv.setUint32(12,h3);
    odv.setUint32(16,h4);odv.setUint32(20,h5);odv.setUint32(24,h6);odv.setUint32(28,h7);
    return out;
  }
  const SBOX = [
    0x63,0x7c,0x77,0x7b,0xf2,0x6b,0x6f,0xc5,0x30,0x01,0x67,0x2b,0xfe,0xd7,0xab,0x76,
    0xca,0x82,0xc9,0x7d,0xfa,0x59,0x47,0xf0,0xad,0xd4,0xa2,0xaf,0x9c,0xa4,0x72,0xc0,
    0xb7,0xfd,0x93,0x26,0x36,0x3f,0xf7,0xcc,0x34,0xa5,0xe5,0xf1,0x71,0xd8,0x31,0x15,
    0x04,0xc7,0x23,0xc3,0x18,0x96,0x05,0x9a,0x07,0x12,0x80,0xe2,0xeb,0x27,0xb2,0x75,
    0x09,0x83,0x2c,0x1a,0x1b,0x6e,0x5a,0xa0,0x52,0x3b,0xd6,0xb3,0x29,0xe3,0x2f,0x84,
    0x53,0xd1,0x00,0xed,0x20,0xfc,0xb1,0x5b,0x6a,0xcb,0xbe,0x39,0x4a,0x4c,0x58,0xcf,
    0xd0,0xef,0xaa,0xfb,0x43,0x4d,0x33,0x85,0x45,0xf9,0x02,0x7f,0x50,0x3c,0x9f,0xa8,
    0x51,0xa3,0x40,0x8f,0x92,0x9d,0x38,0xf5,0xbc,0xb6,0xda,0x21,0x10,0xff,0xf3,0xd2,
    0xcd,0x0c,0x13,0xec,0x5f,0x97,0x44,0x17,0xc4,0xa7,0x7e,0x3d,0x64,0x5d,0x19,0x73,
    0x60,0x81,0x4f,0xdc,0x22,0x2a,0x90,0x88,0x46,0xee,0xb8,0x14,0xde,0x5e,0x0b,0xdb,
    0xe0,0x32,0x3a,0x0a,0x49,0x06,0x24,0x5c,0xc2,0xd3,0xac,0x62,0x91,0x95,0xe4,0x79,
    0xe7,0xc8,0x37,0x6d,0x8d,0xd5,0x4e,0xa9,0x6c,0x56,0xf4,0xea,0x65,0x7a,0xae,0x08,
    0xba,0x78,0x25,0x2e,0x1c,0xa6,0xb4,0xc6,0xe8,0xdd,0x74,0x1f,0x4b,0xbd,0x8b,0x8a,
    0x70,0x3e,0xb5,0x66,0x48,0x03,0xf6,0x0e,0x61,0x35,0x57,0xb9,0x86,0xc1,0x1d,0x9e,
    0xe1,0xf8,0x98,0x11,0x69,0xd9,0x8e,0x94,0x9b,0x1e,0x87,0xe9,0xce,0x55,0x28,0xdf,
    0x8c,0xa1,0x89,0x0d,0xbf,0xe6,0x42,0x68,0x41,0x99,0x2d,0x0f,0xb0,0x54,0xbb,0x16];
  const INV_SBOX = (function(){ const a=new Uint8Array(256); for(let i=0;i<256;i++) a[SBOX[i]]=i; return a; })();
  const RCON = [0x01,0x02,0x04,0x08,0x10,0x20,0x40,0x80,0x1b,0x36];
  function xtime(b){ b &= 0xff; const r=(b<<1)&0xff; return (b&0x80)?(r^0x1b):r; }
  function gmul(a,b){ let p=0; a&=0xff; b&=0xff; for(let i=0;i<8;i++){ if(b&1) p^=a; const hi=a&0x80; a=(a<<1)&0xff; if(hi) a^=0x1b; b>>=1; } return p&0xff; }
  function rotWord(t){ const x=t[0]; t[0]=t[1]; t[1]=t[2]; t[2]=t[3]; t[3]=x; }
  function subWord(t){ for(let i=0;i<4;i++) t[i]=SBOX[t[i]]; }
  function keyExpansion(key){
    const Nk = key.length/4, Nr = Nk+6, Nb=4;
    const w = new Uint8Array(Nb*(Nr+1)*4);
    for(let i=0;i<key.length;i++) w[i]=key[i];
    const temp = new Uint8Array(4);
    for(let i=Nk;i<Nb*(Nr+1);i++){
      for(let t=0;t<4;t++) temp[t]=w[(i-1)*4+t];
      if(i%Nk===0){ rotWord(temp); subWord(temp); temp[0]^=RCON[i/Nk-1]; }
      else if(Nk>6 && i%Nk===4){ subWord(temp); }
      for(let t=0;t<4;t++) w[i*4+t]=w[(i-Nk)*4+t]^temp[t];
    }
    return { w:w, Nr:Nr };
  }
  function addRoundKey(s,w,r){ for(let i=0;i<16;i++) s[i]^=w[r*16+i]; }
  function subBytes(s){ for(let i=0;i<16;i++) s[i]=SBOX[s[i]]; }
  function invSubBytes(s){ for(let i=0;i<16;i++) s[i]=INV_SBOX[s[i]]; }
  function shiftRows(s){ for(let r=1;r<4;r++){ const row=[s[r],s[r+4],s[r+8],s[r+12]]; for(let c=0;c<4;c++) s[r+c*4]=row[(c+r)%4]; } }
  function invShiftRows(s){ for(let r=1;r<4;r++){ const row=[s[r],s[r+4],s[r+8],s[r+12]]; for(let c=0;c<4;c++) s[r+c*4]=row[(c-r+4)%4]; } }
  function mixColumns(s){
    for(let c=0;c<4;c++){ const i=c*4; const a0=s[i],a1=s[i+1],a2=s[i+2],a3=s[i+3];
      s[i]   = xtime(a0)^(xtime(a1)^a1)^a2^a3;
      s[i+1] = a0^xtime(a1)^(xtime(a2)^a2)^a3;
      s[i+2] = a0^a1^xtime(a2)^(xtime(a3)^a3);
      s[i+3] = (xtime(a0)^a0)^a1^a2^xtime(a3);
    }
  }
  function invMixColumns(s){
    for(let c=0;c<4;c++){ const i=c*4; const a0=s[i],a1=s[i+1],a2=s[i+2],a3=s[i+3];
      s[i]   = gmul(a0,14)^gmul(a1,11)^gmul(a2,13)^gmul(a3,9);
      s[i+1] = gmul(a0,9)^gmul(a1,14)^gmul(a2,11)^gmul(a3,13);
      s[i+2] = gmul(a0,13)^gmul(a1,9)^gmul(a2,14)^gmul(a3,11);
      s[i+3] = gmul(a0,11)^gmul(a1,13)^gmul(a2,9)^gmul(a3,14);
    }
  }
  function cipher(input,w,Nr){
    const s=input.slice(0,16); addRoundKey(s,w,0);
    for(let r=1;r<Nr;r++){ subBytes(s); shiftRows(s); mixColumns(s); addRoundKey(s,w,r); }
    subBytes(s); shiftRows(s); addRoundKey(s,w,Nr); return s;
  }
  function invCipher(input,w,Nr){
    const s=input.slice(0,16); addRoundKey(s,w,Nr);
    for(let r=Nr-1;r>0;r--){ invShiftRows(s); invSubBytes(s); addRoundKey(s,w,r); invMixColumns(s); }
    invShiftRows(s); invSubBytes(s); addRoundKey(s,w,0); return s;
  }
  function aesCbcEncrypt(key, iv, plaintext){
    const { w, Nr } = keyExpansion(key);
    const pad = 16 - (plaintext.length % 16);
    const data = new Uint8Array(plaintext.length + pad); data.set(plaintext);
    for(let i=plaintext.length;i<data.length;i++) data[i]=pad;
    const out = new Uint8Array(data.length); let prev = iv.slice(0,16);
    for(let off=0; off<data.length; off+=16){
      const blk = new Uint8Array(16);
      for(let i=0;i<16;i++) blk[i]=data[off+i]^prev[i];
      const e = cipher(blk,w,Nr);
      for(let i=0;i<16;i++){ out[off+i]=e[i]; prev[i]=e[i]; }
    }
    return out;
  }
  function aesCbcDecrypt(key, iv, ciphertext){
    const { w, Nr } = keyExpansion(key);
    const out = new Uint8Array(ciphertext.length); let prev = iv.slice(0,16);
    for(let off=0; off<ciphertext.length; off+=16){
      const blk = ciphertext.slice(off, off+16);
      const d = invCipher(blk,w,Nr);
      for(let i=0;i<16;i++) out[off+i]=d[i]^prev[i];
      prev = blk;
    }
    const pad = out[out.length-1];
    return out.slice(0, out.length - (pad > 0 && pad <= 16 ? pad : 0));
  }
  /**
   * 分片解密（2026-11-05 加）：同一套算法，每 256KB 让出一次主线程。
   *
   * 为什么需要：全量备份里的服务端数据 + 附件 base64 常有 10~50MB，纯 JS
   * AES 只有 ~0.5 MB/s —— 同步解密 20MB 要 35 秒以上，期间主线程完全占死，
   * 页面定格、按钮点不动，用户看到的就是「本地数据导入卡死」。
   * 切成分片后总耗时基本不变，但界面能转、进度能走、能取消。
   *
   * 不足 16 字节的尾部用 padding 处理，与同步版逐字节一致（b25 有对拍）。
   */
  function aesCbcDecryptAsync(key, iv, ciphertext, onProgress, chunkBytes){
    /* 默认 64KB ≈ 每块 120ms（纯 JS 约 0.5MB/s）：小块才能让浏览器在块间
       真正刷新一次。256KB 一块要 470ms，实测定时器只跳 8 次，界面还是顿。 */
    const CH = Math.max(16, ((chunkBytes || 65536) >> 4) << 4);   /* 16 字节对齐 */
    const { w, Nr } = keyExpansion(key);
    const out = new Uint8Array(ciphertext.length);
    let prev = iv.slice(0,16);
    let off = 0;
    return new Promise(function(resolve, reject){
      function step(){
        try{
          const end = Math.min(ciphertext.length, off + CH);
          for(; off < end; off += 16){
            const blk = ciphertext.slice(off, off+16);
            const d = invCipher(blk,w,Nr);
            for(let i=0;i<16;i++) out[off+i]=d[i]^prev[i];
            prev = blk;
          }
          if(onProgress) onProgress(ciphertext.length ? off/ciphertext.length : 1);
          if(off < ciphertext.length){ setTimeout(step, 0); return; }
          const pad = out[out.length-1];
          resolve(out.slice(0, out.length - (pad > 0 && pad <= 16 ? pad : 0)));
        }catch(e){ reject(e); }
      }
      setTimeout(step, 0);
    });
  }
  function hmacSha256(key, msg){
    const blockSize=64; let k=key;
    if(k.length>blockSize) k=sha256(k);
    const kPad=new Uint8Array(blockSize); kPad.set(k);
    const oKey=new Uint8Array(blockSize), iKey=new Uint8Array(blockSize);
    for(let i=0;i<blockSize;i++){ oKey[i]=kPad[i]^0x5c; iKey[i]=kPad[i]^0x36; }
    const inner=sha256(concat(iKey,msg));
    return sha256(concat(oKey,inner));
  }
  function pbkdf2(password, salt, iterations, dkLen){
    const hLen=32, l=Math.ceil(dkLen/hLen), r=dkLen-(l-1)*hLen, DK=new Uint8Array(dkLen);
    for(let i=1;i<=l;i++){
      const saltI=concat(salt, uint32be(i));
      let U=hmacSha256(password,saltI), T=U.slice();
      for(let j=2;j<=iterations;j++){ U=hmacSha256(password,U); for(let k=0;k<32;k++) T[k]^=U[k]; }
      DK.set(T,(i-1)*hLen);
    }
    return DK;
  }
  function concat(a,b){ const o=new Uint8Array(a.length+b.length); o.set(a); o.set(b,a.length); return o; }
  function uint32be(n){ return new Uint8Array([(n>>>24)&0xff,(n>>>16)&0xff,(n>>>8)&0xff,n&0xff]); }
  function bytesToB64(u8){
    let bin=''; const chunk=0x8000;
    for(let i=0;i<u8.length;i+=chunk){ bin+=String.fromCharCode.apply(null, u8.subarray(i,i+chunk)); }
    if(typeof btoa==='function') return btoa(bin);
    return Buffer.from(u8).toString('base64');
  }
  function b64ToBytes(s){
    if(typeof atob==='function'){ const bin=atob(s); const o=new Uint8Array(bin.length); for(let i=0;i<bin.length;i++) o[i]=bin.charCodeAt(i); return o; }
    return new Uint8Array(Buffer.from(s,'base64'));
  }
  const api = { sha256, aesCbcEncrypt, aesCbcDecrypt, aesCbcDecryptAsync,
                hmacSha256, pbkdf2, bytesToB64, b64ToBytes };
  root.JS_CIPHER = api;
})(typeof window !== 'undefined' ? window : globalThis);
