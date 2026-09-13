'use strict';
// Exercise the exact browser predicate without a DOM dependency.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../web/app.js'),'utf8');
const start=source.indexOf('function draftIsDirty('),end=source.indexOf('async function navigate(',start);
assert.ok(start>=0&&end>start);
const context={state:{draftEditor:null},Error};vm.createContext(context);vm.runInContext(source.slice(start,end),context);
assert.equal(context.draftIsDirty(null),false);
const editor={body:{value:'body'},subject:{value:'subject'},recipient:{value:'recipient'},analysisId:'analysis',savedBody:'body',savedSubject:'subject',savedRecipient:'recipient',savedAnalysis:'analysis'};
context.state.draftEditor=editor;assert.equal(context.draftIsDirty(editor),false);context.guardDraft();
for(const field of ['body','subject','recipient']){
  const old=editor[field].value;editor[field].value='changed';assert.equal(context.draftIsDirty(editor),true);assert.throws(()=>context.guardDraft(),/chưa lưu/);editor[field].value=old;
}
editor.analysisId='new-analysis';assert.equal(context.draftIsDirty(editor),true);
console.log('Draft change guard: passed');
