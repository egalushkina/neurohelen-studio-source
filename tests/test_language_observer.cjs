const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync(require('path').join(__dirname,'../webstudio/static/languages.js'),'utf8');
let writes=0, observer, queue=[];
const options=['ru','en','uk'].map(value=>({value,_text:'',get textContent(){return this._text},set textContent(v){this._text=v;writes++;if(observer)observer()}}));
const context={navigator:{languages:['ru']},localStorage:{getItem:()=>null},queueMicrotask:f=>queue.push(f),MutationObserver:class{constructor(f){observer=f}observe(){}},document:{body:{},documentElement:{},querySelector:()=>null,querySelectorAll:s=>s==='#adviser-language option'?options:[]}};
vm.createContext(context);vm.runInContext(source,context);
for(const lang of ['ru','en','uk','ru']){
 vm.runInContext(`localizeUI('${lang}')`,context);
 let iterations=0;while(queue.length&&iterations++<10)queue.shift()();
 assert.equal(queue.length,0,'Localization must stop scheduling itself');
 const before=writes;vm.runInContext(`localizeUI('${lang}')`,context);
 assert.equal(writes,before,'Same language must not rewrite option nodes');
}
console.log('PASS: localization settles in all three languages');
