'use strict';
(()=>{
 const supported=['ru','uk','en'];
 const lang=document.documentElement.lang;
 const remember=l=>{try{localStorage.setItem('studio.uiLanguage',l);sessionStorage.setItem('studio.pendingLanguage',l)}catch{}};
 if(document.documentElement.dataset.autoLanguage==='true'){
  let saved;try{saved=localStorage.getItem('studio.uiLanguage')}catch{}
  const device=(navigator.languages?.[0]||navigator.language||'en').toLowerCase().split('-')[0];
  const preferred=supported.includes(saved)?saved:supported.includes(device)?device:'en';
  location.replace('/'+preferred+'/');return;
 }
 document.querySelectorAll('[data-language]').forEach(a=>a.addEventListener('click',()=>remember(a.dataset.language)));
 document.querySelectorAll('[data-enter]').forEach(a=>a.addEventListener('click',()=>remember(lang)));
})();
