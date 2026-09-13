'use strict';
const $ = id => document.getElementById(id);
const state = {user:null, page:'queue', analysis:null, key:null, busy:false, caseId:null, filter:'active', query:'', mine:false, draftEditor:null};
const statusNames = {ready_for_review:'Sẵn sàng duyệt', needs_clarification:'Cần làm rõ', insufficient_evidence:'Chưa đủ bằng chứng', dependency_unavailable:'Dịch vụ chưa sẵn sàng', open:'Đang mở'};
const actionNames = {notify_customer:'Chuẩn bị thông báo khách hàng', contact_carrier:'Liên hệ hãng vận chuyển', escalate_manager:'Chuyển quản lý xem xét', hold_for_manager:'Chờ quyết định của quản lý', notify_account_owner:'Liên hệ người quản lý tài khoản'};
const titles = {queue:'Hàng đợi xử lý',analysis:'Phân tích sự cố',history:'Lịch sử phân tích',tickets:'Ticket xử lý',documents:'Kho quy trình',usage:'Sử dụng AI'};
const caseStates={new:'Chưa tiếp nhận',investigating:'Đang xử lý',waiting_carrier:'Chờ hãng vận chuyển',escalated:'Đã chuyển cấp',resolved:'Đã giải quyết'};
const transitions={new:['investigating'],investigating:['waiting_carrier','escalated','resolved'],waiting_carrier:['investigating','escalated','resolved'],escalated:['investigating','waiting_carrier','resolved'],resolved:['investigating']};
Object.assign(statusNames,caseStates);
function node(tag, text, cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
function button(text, action, cls=''){const b=node('button',text,cls);b.type='button';b.onclick=()=>run(action);return b;}
function badge(status){return node('span',statusNames[status]||status,'badge '+(status==='dependency_unavailable'?'error':status==='ready_for_review'||status==='open'?'':'warning'));}
function message(text,error=false){$('notice').replaceChildren(text?node('div',text,error?'error-box':'success-box'):node('span'));}
function date(text){return text?new Date(text).toLocaleString('vi-VN'):'Chưa có thời gian';}
async function api(path,body,method='POST',headers={}){
  const options={method:body===undefined?'GET':method,credentials:'same-origin',headers};
  if(body!==undefined){options.body=JSON.stringify(body);options.headers={'Content-Type':'application/json',...headers};}
  const response=await fetch('/api'+path,options);
  const data=await response.json();
  if(!response.ok){
    if(response.status===401){state.user=null;showSession();}
    const detail=Array.isArray(data.detail)?data.detail.map(d=>`${d.loc.slice(1).join('.')}: ${d.msg}`).join('; '):data.detail;
    throw new Error(response.status===409?'Dữ liệu đã thay đổi. Tải lại và kiểm tra trước khi tiếp tục.':detail||`Lỗi HTTP ${response.status}`);
  }return data;
}
async function run(action){
  if(state.busy)return;state.busy=true;document.body.setAttribute('aria-busy','true');
  try{await action();}catch(error){message(error.message||'Không thể kết nối. Vui lòng thử lại.',true);}
  finally{state.busy=false;document.body.removeAttribute('aria-busy');}
}
function showSession(){
  $('login').hidden=!!state.user;$('content').hidden=!state.user;$('logout').hidden=!state.user;
  $('session-label').textContent=state.user?`${state.user.tenant_id==='A'?'Alpha Logistics':'Beta Logistics'} / ${state.user.role==='operator'?'Nhân viên vận hành':'Chỉ xem'}`:'Chưa đăng nhập';
  if(!state.user){state.analysis=null;state.key=null;state.caseId=null;state.draftEditor=null;$('content').replaceChildren();}
}
function heading(subtitle){
  const wrap=node('div',undefined,'page-heading'),left=node('div');left.append(node('h1',titles[state.page]),node('p',subtitle));wrap.append(left);$('content').replaceChildren(wrap);return wrap;
}
function field(form,label,value='',type='text',name=''){
  const id='field-'+crypto.randomUUID(),wrap=node('div'),l=node('label',label);l.htmlFor=id;
  const input=document.createElement(type==='textarea'?'textarea':'input');input.id=id;input.name=name;
  if(type!=='textarea')input.type=type;input.value=value;wrap.append(l,input);form.append(wrap);return input;
}
function selectField(form,label,options,value){const wrap=node('div'),id='field-'+crypto.randomUUID(),l=node('label',label),s=node('select');l.htmlFor=id;s.id=id;for(const [v,t] of options){const o=node('option',t);o.value=v;s.append(o);}s.value=value;wrap.append(l,s);form.append(wrap);return s;}
function empty(parent,title,detail){const wrap=node('div',undefined,'empty');wrap.append(node('div','↳','symbol'),node('h2',title),node('p',detail));parent.append(wrap);}
function draftIsDirty(editor){return !!editor&&(editor.body.value!==editor.savedBody||editor.subject.value!==editor.savedSubject||editor.recipient.value!==editor.savedRecipient||editor.analysisId!==editor.savedAnalysis);}
function guardDraft(){if(draftIsDirty(state.draftEditor))throw new Error('Bản nháp có thay đổi chưa lưu. Hãy lưu hoặc chọn “Bỏ thay đổi chưa lưu” trước khi rời hồ sơ.');}
async function navigate(page){guardDraft();state.draftEditor=null;state.page=page;state.caseId=null;message('');document.querySelectorAll('[data-page]').forEach(b=>b.setAttribute('aria-current',b.dataset.page===page?'page':'false'));if(!state.user)return;if(page==='queue')await queue();else if(page==='analysis')renderAnalysis();else if(page==='documents')await documents();else if(page==='usage')await usage();else await history(page);}
async function usage(){
  heading('Lượt gọi của tài khoản đang đăng nhập. Không phải hóa đơn hoặc giới hạn chi phí USD.');
  const data=await api('/usage'),panel=node('section',undefined,'panel');
  panel.append(button('Tải lại số liệu',()=>usage(),'quiet'));
  if(!data.summary){empty(panel,data.mode==='openai'?'Chưa có nhật ký sử dụng':'Đang chạy offline',data.mode==='openai'?'Không có số liệu để xác minh chi phí.':'Phân tích dùng dữ liệu giả lập và quy tắc cố định, không gọi GPT.');$('content').append(panel);return;}
  const s=data.summary,metrics=node('div',undefined,'queue-metrics');
  for(const [label,value] of [['Lượt đã giữ chỗ',s.requests],['Còn theo tài khoản',s.remaining_requests],['Lượt lỗi',s.failed],['Chưa kết thúc',s.reserved]]){const item=node('div',undefined,'metric');item.append(node('span',label),node('strong',String(value)));metrics.append(item);}
  panel.append(node('p',`Giới hạn tài khoản: ${s.request_limit} lượt trong toàn bộ lịch sử. Giới hạn chung của hệ thống cũng có thể chặn lượt mới. Lượt lỗi hoặc bị gián đoạn không tự hoàn lại.`, 'hint'));
  panel.append(node('p',`Token đã được báo cáo: đầu vào ${s.input_tokens??'chưa biết'} · đầu ra ${s.output_tokens??'chưa biết'}. ${s.unknown_usage} lượt chưa có đủ số liệu; tổng có thể chưa đầy đủ.`, 'hint'));
  panel.append(node('p',s.pricing_configured?`Chi phí hạch toán cục bộ của tài khoản: $${s.accounted_cost_usd.toFixed(6)} USD. Đây không phải hóa đơn của nhà cung cấp.`:'Chưa cấu hình đơn giá; không hiển thị ước tính chi phí giả.', 'hint'));
  panel.append(node('h2','20 lượt gần nhất'));
  if(!s.items.length)empty(panel,'Chưa có lượt gọi','Nhật ký sẽ xuất hiện khi GPT thực sự được gọi.');
  const names={completed:'Đã nhận kết quả',failed:'Lỗi / kết quả bị từ chối',reserved:'Đang xử lý hoặc đã gián đoạn'};
  for(const item of s.items){const row=node('div',undefined,'row'),info=node('div');info.append(node('strong',item.model),node('p',date(item.started_at)),node('small',`Token vào / ra: ${item.input_tokens??'?'} / ${item.output_tokens??'?'}`,'muted'));row.append(info,node('span',names[item.status]||item.status,'badge'));panel.append(row);}
  $('content').append(metrics,panel);
}
function sla(due,status){if(status==='resolved')return 'Đã kết thúc';const mins=Math.ceil((new Date(due)-Date.now())/60000);const n=Math.abs(mins);return (mins<0?'Quá hạn ':'Còn ')+(n<60?n+' phút':Math.floor(n/60)+' giờ '+n%60+' phút');}
async function queue(){
  guardDraft();state.draftEditor=null;
  state.caseId=null;
  const data=await api('/cases?'+new URLSearchParams({status:state.filter,q:state.query,mine:state.mine}));
  const head=heading('Ca trực điều phối · Ưu tiên hồ sơ sắp quá hạn, xác minh trước khi hành động.');
  head.append(button('↻ Cập nhật',()=>queue(),'quiet'));
  const metrics=node('div',undefined,'queue-metrics');
  for(const [key,label] of [['active','Hồ sơ đang mở'],['overdue','Quá hạn phản hồi'],['unassigned','Chưa có người xử lý'],['resolved','Đã giải quyết']]){
    const item=node('div',undefined,'metric '+key);item.append(node('span',label),node('strong',String(data.metrics[key])));metrics.append(item);
  }
  const form=node('form',undefined,'queue-toolbar');
  const q=field(form,'Tìm hồ sơ',state.query);q.placeholder='Khách hàng, lô hàng, hãng vận chuyển…';
  const filter=selectField(form,'Trạng thái',[['active','Đang mở'],['all','Tất cả'],...Object.entries(caseStates)],state.filter);
  const owner=selectField(form,'Người phụ trách',[['all','Cả nhóm'],['mine','Của tôi']],state.mine?'mine':'all');
  form.append(node('button','Lọc hồ sơ','primary'));form.onsubmit=e=>{e.preventDefault();run(async()=>{state.query=q.value;state.filter=filter.value;state.mine=owner.value==='mine';await queue();});};
  const panel=node('section',undefined,'queue-table');
  const header=node('div',undefined,'queue-row table-head');for(const label of ['HỒ SƠ / KHÁCH HÀNG','TUYẾN VẬN CHUYỂN','TRẠNG THÁI','HẠN PHẢN HỒI',''])header.append(node('span',label));panel.append(header);
  for(const c of data.items){
    const row=node('div',undefined,'queue-row');const subject=node('div'),route=node('div'),status=node('div'),due=node('div');
    subject.append(node('small',c.id+' · '+c.shipment_id),node('strong',c.customer),node('span',({critical:'Khẩn cấp',high:'Ưu tiên cao',normal:'Thông thường'}[c.priority])+' · '+c.customer_tier,'muted'));
    route.append(node('strong',c.origin+' → '+c.destination),node('span',c.carrier,'muted'));
    status.append(badge(c.status),node('small',c.owner?'Phụ trách: '+c.owner:'Chưa phân công','muted'));
    due.append(node('strong',sla(c.due_at,c.status),c.status!=='resolved'&&new Date(c.due_at)<new Date()?'overdue-text':''),node('small',date(c.due_at),'muted'));
    row.append(subject,route,status,due,button('Mở hồ sơ',()=>openCase(c.id),'quiet'));panel.append(row);
  }
  if(!data.items.length)empty(panel,'Không có hồ sơ phù hợp','Đổi bộ lọc hoặc kiểm tra hàng đợi của cả nhóm.');
  $('content').append(metrics,form,panel,node('p','Kịch bản giả lập · SLA là mục tiêu nội bộ của bản demo · Không kết nối email hoặc hãng vận chuyển thật.','hint'));
}
async function openCase(id,keepAnalysis=false){
  if(state.draftEditor?.caseId!==id)guardDraft();
  const previous=state.draftEditor;
  const pending=previous?.caseId===id&&draftIsDirty(previous)?{...previous,bodyText:previous.body.value,subjectText:previous.subject.value,recipientText:previous.recipient.value}:null;
  const c=await api('/cases/'+id),drafts=await api('/cases/'+id+'/drafts');state.caseId=id;state.page='queue';message('');
  if(!keepAnalysis){state.analysis=null;state.key=null;}
  const head=heading(c.id+' / '+c.shipment_id+' / Hồ sơ giả lập');head.querySelector('h1').textContent=c.customer;
  head.append(button('← Hàng đợi',()=>queue(),'quiet'));
  const summary=node('div',undefined,'case-summary');summary.append(badge(c.status),node('strong',c.origin+' → '+c.destination),node('span',sla(c.due_at,c.status)),node('span',c.owner||'Chưa phân công'));
  const layout=node('div',undefined,'case-layout'),left=node('div',undefined,'case-main'),right=node('aside',undefined,'case-assistant');
  const context=node('section',undefined,'panel');context.append(node('span','THÔNG TIN VẬN CHUYỂN','section-tag'),node('h2',c.shipment_id+' · '+c.carrier));
  const facts=node('div',undefined,'facts');for(const [label,value] of [['Khách hàng',c.customer+' · '+c.customer_tier],['Dự kiến ban đầu',date(c.shipment.promised_at)],['ETA cập nhật',date(c.shipment.estimated_at)],['Hạn phản hồi nội bộ',date(c.due_at)]]){const f=node('div',undefined,'fact');f.append(node('span',label),node('strong',value));facts.append(f);}context.append(facts);
  const notice=node('section',undefined,'panel');notice.append(node('span','THÔNG BÁO NGUỒN · GIẢ LẬP','section-tag'),node('h2',c.notice.subject),node('p',c.notice.sender+' · '+date(c.notice.received_at),'muted'),node('blockquote',c.notice.body));
  const orders=node('section',undefined,'panel');orders.append(node('h2','Đơn hàng liên quan'));
  for(const o of c.order_items){const r=node('div',undefined,'row');const info=node('div');info.append(node('strong',o.id),node('p',o.product+' · '+o.quantity+' đơn vị'));r.append(info,node('span',new Intl.NumberFormat('vi-VN',{style:'currency',currency:'VND'}).format(o.value_vnd)),node('span',o.status==='active'?'Đang thực hiện':'Đã hủy','badge'));orders.append(r);}
  const activity=node('section',undefined,'panel');activity.append(node('h2','Nhật ký xử lý'));
  for(const e of [...c.events].reverse()){const item=node('div',undefined,'timeline-event');item.append(node('small',date(e.at)+' · '+e.actor,'muted'),node('p',e.note||({claim:'Nhận xử lý hồ sơ',attach_ticket:'Liên kết ticket đã duyệt'}[e.action]||e.action)),node('small',caseStates[e.value]||e.value||'','muted'));activity.append(item);}
  if(c.ticket_ids.length){activity.append(node('h3','Ticket đã duyệt'));for(const ticket of c.ticket_ids)activity.append(node('p',ticket,'trace'));}
  const controls=node('section',undefined,'panel');controls.append(node('span','BƯỚC TIẾP THEO','section-tag'),node('h2','Xử lý hồ sơ'));
  const mutate=async(action,value='',note='')=>{await api('/cases/'+id+'/events',{expected_revision:c.revision,action,value,note});await openCase(id,true);};
  if(state.user.role==='operator'){
    if(!c.owner)controls.append(button('Nhận xử lý',()=>mutate('claim'),'primary'));
    const form=node('form'),note=field(form,'Ghi nhận kết quả hoặc yêu cầu tiếp theo','','textarea');note.required=true;note.maxLength=4000;note.rows=3;
    const next=selectField(form,'Hành động',[['note','Chỉ thêm ghi chú'],...transitions[c.status].map(s=>[s,caseStates[s]])],'note');
    form.append(node('button','Lưu cập nhật','primary'));form.onsubmit=e=>{e.preventDefault();run(()=>mutate(next.value==='note'?'note':'transition',next.value==='note'?'':next.value,note.value));};controls.append(form);
  }else controls.append(node('p','Tài khoản chỉ xem; không được thay đổi hồ sơ.'));
  const analysis=node('section',undefined,'panel');analysis.append(node('span','OPS ASSISTANT','section-tag'),node('h2','Đề xuất có căn cứ'),node('p','Đối chiếu thông báo hãng vận chuyển với đơn hàng và quy trình được phép truy cập.','muted'));
  analysis.append(button('Phân tích hồ sơ',async()=>{message('Đang đối chiếu hồ sơ…');state.analysis=await api('/analyses',{message:c.notice.body});state.key=crypto.randomUUID();renderResult();message('');},'primary'));
  const result=node('div');result.id='result';analysis.append(result);right.append(controls,analysis);left.append(context,notice,orders,draftEditor(c,drafts,pending),activity);layout.append(left,right);$('content').append(summary,layout);renderResult();
}
function draftEditor(c,drafts,pending){
  const latest=drafts[0],panel=node('section',undefined,'panel'),form=node('form');
  panel.append(node('span','TRAO ĐỔI · CHƯA GỬI','section-tag'),node('h2','Bản nháp phản hồi'),node('p','Nội dung do người vận hành chỉnh sửa. Lưu tại đây không đồng nghĩa với gửi hoặc duyệt phương án.','hint'));
  const recipient=field(form,'Người nhận / bộ phận',pending?.recipientText||latest?.recipient_label||c.customer);
  const subject=field(form,'Tiêu đề bản nháp',pending?.subjectText||latest?.subject||'Cập nhật tiến độ · '+c.shipment_id);
  const body=field(form,'Nội dung bản nháp',pending?.bodyText??latest?.body??'','textarea');body.rows=7;body.maxLength=8000;recipient.maxLength=160;subject.maxLength=200;
  for(const input of [recipient,subject,body])input.required=true;
  const editor={caseId:c.id,version:pending?.version??latest?.version??0,analysisId:pending?.analysisId||latest?.analysis_id||null,savedAnalysis:pending?pending.savedAnalysis:latest?.analysis_id||null,recipient,subject,body,savedBody:pending?.savedBody??latest?.body??'',savedSubject:pending?.savedSubject??subject.value,savedRecipient:pending?.savedRecipient??recipient.value};state.draftEditor=editor;
  if(state.user.role==='operator'){
    const save=node('button','Lưu bản nháp','primary');form.append(save);
    form.onsubmit=e=>{e.preventDefault();run(async()=>{
      if(!editor.analysisId)throw new Error('Hãy phân tích hồ sơ rồi chọn “Đưa vào bản nháp” để gắn nguồn đối chiếu.');
      const result=await api('/cases/'+c.id+'/draft',{expected_version:editor.version,analysis_id:editor.analysisId,recipient_label:recipient.value,subject:subject.value,body:body.value},'PUT');
      editor.savedBody=body.value;editor.savedSubject=subject.value;editor.savedRecipient=recipient.value;editor.savedAnalysis=editor.analysisId;await openCase(c.id,true);message(`Đã lưu bản nháp v${result.version}. Chưa gửi ra ngoài.`);
    });};
    form.append(button('Bỏ thay đổi chưa lưu',async()=>{state.draftEditor=null;await openCase(c.id,true);message('Đã tải lại bản nháp đã lưu; không xóa lịch sử.');},'quiet'));
  }else for(const input of [recipient,subject,body])input.disabled=true;
  form.append(button('Sao chép nội dung',async()=>{await navigator.clipboard.writeText(body.value);message('Đã sao chép nội dung đang hiển thị; không có email nào được gửi.');},'quiet'));panel.append(form);
  if(latest){panel.append(node('p',`Bản lưu mới nhất: v${latest.version} · ${latest.updated_by} · ${date(latest.updated_at)}`,'hint'));const history=node('details',undefined,'source');history.append(node('summary','Lịch sử bản nháp'));for(const d of drafts){const item=node('details',undefined,'source');item.append(node('summary',`v${d.version} · ${date(d.updated_at)} · ${d.updated_by}`),node('strong',d.subject),node('blockquote',d.body));history.append(item);}panel.append(history);}
  return panel;
}
function renderAnalysis(){
  heading('Đối chiếu dữ liệu vận hành và quy trình trước khi quyết định bước tiếp theo.');
  const layout=node('div',undefined,'layout'),inputPanel=node('section',undefined,'panel'),result=node('section',undefined,'panel');result.id='result';
  inputPanel.append(node('div','01 / TÌNH HUỐNG','section-tag'),node('h2','Điều gì đang xảy ra?'));
  const form=node('form'),input=field(form,'Nội dung thông báo','','textarea');input.required=true;input.maxLength=8000;input.placeholder='Ví dụ: Lô SHP-1042 chậm hai ngày. Kiểm tra đơn bị ảnh hưởng và đề xuất xử lý.';
  const examples=node('div',undefined,'examples');for(const [label,text] of [['Giao chậm',`Lô SHP-${state.user.tenant_id==='A'?'1042':'2042'} chậm hai ngày.`],['Thiếu thông tin','Lô hàng đang chậm.'],['Mâu thuẫn quy trình','Lô SHP-1043 bị chậm.']])examples.append(button(label,()=>{input.value=text;input.focus();}));
  const submit=node('button','Phân tích sự cố →','primary');submit.type='submit';
  form.append(examples,submit,node('p','Chưa có hành động nào được thực hiện khi phân tích.','hint'));
  form.onsubmit=e=>{e.preventDefault();run(async()=>{message('Đang đối chiếu dữ liệu và quy trình…');state.analysis=await api('/analyses',{message:input.value});state.key=crypto.randomUUID();renderResult();message('');});};
  inputPanel.append(form);layout.append(inputPanel,result);$('content').append(layout);renderResult();
}
function renderResult(){
  const box=$('result');if(!box)return;box.replaceChildren();const a=state.analysis;
  if(!a){empty(box,'Mỗi quyết định cần một căn cứ','Kết quả phân tích, đơn liên quan và trích dẫn sẽ xuất hiện tại đây.');return;}
  const top=node('div',undefined,'result-top');top.append(node('h2','Kết quả đối chiếu'),badge(a.status));box.append(top);
  if(a.clarification_questions.length)for(const q of a.clarification_questions)box.append(node('p',q));
  const facts=node('div',undefined,'facts');const labels={id:'Lô hàng',status:'Trạng thái hệ thống',promised_at:'Mốc giao dự kiến ban đầu',estimated_at:'Ước tính hiện tại'};
  for(const f of a.verified_facts){const n=node('div',undefined,'fact');n.append(node('span',labels[f.field]||f.field),node('strong',f.field.endsWith('_at')?date(f.value):String(f.value)));facts.append(n);}box.append(facts);
  if(a.affected_orders.length){box.append(node('h3','Đơn bị ảnh hưởng'));for(const order of a.affected_orders)box.append(node('div',`${order.id} · ${order.status}`,'hint'));}
  if(a.citations.length){box.append(node('h3','Nguồn quy trình'));for(const c of a.citations){const d=node('details',undefined,'source');d.append(node('summary',`${c.document_id} · phiên bản ${c.version}`),node('blockquote',c.quote));box.append(d);}}
  for(const action of a.recommended_actions){box.append(node('h3','Bước được đề xuất'),node('p',actionNames[action.action]||action.action));}
  if(a.draft_message){box.append(node('h3','Bản nháp — cần người kiểm tra'),node('div',a.draft_message,'draft'),button('Sao chép bản nháp',async()=>{await navigator.clipboard.writeText(a.draft_message);message('Đã sao chép bản nháp.');},'quiet'));}
  if(a.draft_message&&state.caseId&&state.user.role==='operator')box.append(button('Đưa vào bản nháp',()=>{const editor=state.draftEditor;if(!editor||editor.caseId!==state.caseId)return;editor.analysisId=a.analysis_id;if(!editor.body.value)editor.body.value=a.draft_message;editor.body.focus();message('Đã gắn nguồn phân tích. Nội dung bạn đã nhập được giữ nguyên; hãy kiểm tra rồi lưu.');},'quiet'));
  for(const warning of a.warnings)box.append(node('p',warning,'warning-text'));
  if(a.status==='ready_for_review'){
    const area=node('div',undefined,'actions'),label=node('label',undefined,'check'),check=node('input');check.type='checkbox';label.append(check,document.createTextNode('Tôi đã kiểm tra dữ kiện và quy trình. Chỉ tạo ticket nội bộ, không gửi email.'));
    const b=button('Duyệt & tạo ticket',async()=>{const ticket=await api(`/analyses/${a.analysis_id}/approve`,{proposal_revision:a.revision,confirmed:true},'POST',{'Idempotency-Key':state.key});if(state.caseId){const c=await api('/cases/'+state.caseId);await api('/cases/'+state.caseId+'/events',{expected_revision:c.revision,action:'attach_ticket',value:ticket.ticket_id,note:'Đã kiểm tra bằng chứng và duyệt phương án. Ticket nội bộ, chưa gửi thông báo.'});await openCase(state.caseId,true);}message(`Đã tạo ticket ${ticket.ticket_id}. Không có email nào được gửi.`);b.textContent='Ticket đã được tạo';check.checked=false;b.disabled=true;});b.className='primary';b.disabled=true;
    check.disabled=state.user.role!=='operator';check.onchange=()=>b.disabled=!check.checked;area.append(label,b);
    if(state.user.role!=='operator')area.append(node('p','Tài khoản chỉ xem không được duyệt ticket.','hint'));box.append(area);
  }box.append(node('div',`Trace ${a.trace_id}`,'trace'));
}
async function history(page,before=null){
  heading(page==='history'?'Các lần phân tích của bạn, được lưu cùng bằng chứng tại thời điểm xử lý.':'Ticket nội bộ trong không gian làm việc hiện tại.');
  const panel=node('section',undefined,'panel'),data=await api(`/${page==='history'?'analyses':'tickets'}?limit=20${before?'&before='+before:''}`);
  if(!data.items.length)empty(panel,'Chưa có mục nào','Phân tích một sự cố và duyệt đề xuất để bắt đầu.');
  for(const item of data.items){const row=node('div',undefined,'row'),info=node('div');info.append(node('strong',page==='history'?(item.shipment_id||'Chưa xác định lô'):item.ticket_id),node('p',date(item.created_at)));row.append(info,badge(item.status),button('Xem chi tiết',async()=>{
    if(page==='history'){state.analysis=await api('/analyses/'+item.analysis_id);state.key=crypto.randomUUID();await navigate('analysis');}
    else{const t=await api('/tickets/'+item.ticket_id);const details=node('div',undefined,'draft');details.append(node('strong',`Ticket ${t.ticket_id}`),node('p',`Người tạo: ${t.created_by} · ${date(t.created_at)}`));for(const a of t.actions)details.append(node('p',actionNames[a.action]||a.action));row.replaceChildren(details);}
  }));panel.append(row);}
  if(data.next_cursor)panel.append(button('Xem các mục cũ hơn',()=>history(page,data.next_cursor)));
  if(before)panel.append(button('Về mới nhất',()=>history(page)));$('content').append(panel);
}
async function documents(after=''){
  const head=heading('Quy trình có phiên bản, phạm vi áp dụng và quyền truy cập rõ ràng.');
  if(state.user.role==='operator')head.append(button('+ Thêm quy trình',()=>documentEditor(null),'primary'));
  const search=node('form',undefined,'toolbar'),ship=node('input'),query=node('input'),searchButton=node('button','Tìm trong quy trình');ship.placeholder='Mã lô hàng';ship.value=state.user.tenant_id==='A'?'SHP-1042':'SHP-2042';ship.setAttribute('aria-label','Mã lô hàng để tra quy trình');query.placeholder='Từ khóa cần tra cứu';query.setAttribute('aria-label','Từ khóa');query.required=true;search.append(ship,query,searchButton);
  const results=node('section',undefined,'panel');
  search.onsubmit=e=>{e.preventDefault();run(async()=>{const hits=await api('/documents/search?'+new URLSearchParams({q:query.value,shipment_id:ship.value}));results.replaceChildren();if(!hits.length)empty(results,'Không có kết quả phù hợp','Thử từ khóa khác hoặc kiểm tra phạm vi của lô hàng.');for(const h of hits){const d=node('div',undefined,'source');d.append(node('strong',h.document_id),node('blockquote',h.quote),node('p',`BM25 ${h.score.toFixed(2)} · phiên bản ${h.version}`,'hint'));results.append(d);}});};
  $('content').append(search,results);
  const page=await api('/documents?limit=20'+(after?'&after='+encodeURIComponent(after):''));
  if(!page.items.length)empty(results,'Kho quy trình trống','Nhân viên vận hành có thể thêm quy trình đầu tiên.');
  for(const doc of page.items){const row=node('div',undefined,'row'),info=node('div');info.append(node('strong',doc.title||doc.id),node('p',`${doc.policy_scope} · v${doc.version} · ${doc.kind==='procedure'?'Quy trình':'Ghi chú không có thẩm quyền'}`));row.append(info,button('Mở tài liệu',()=>documentEditor(doc)));results.append(row);}
  if(page.next_cursor)results.append(button('Xem thêm tài liệu',()=>documents(page.next_cursor)));
  if(after)results.append(button('Về đầu danh sách',()=>documents()));
}
async function documentEditor(doc){
  message('');
  heading(doc?`${doc.id} / Phiên bản ${doc.version}`:'Tạo quy trình mới. Kiểm tra phạm vi áp dụng trước khi lưu.');
  const panel=node('section',undefined,'panel'),form=node('form'),grid=node('div',undefined,'form-grid');
  const title=field(grid,'Tiêu đề',doc?.title||doc?.id||''),scope=field(grid,'Phạm vi chính sách',doc?.policy_scope||'standard');
  const action=selectField(grid,'Bước xử lý',Object.entries(actionNames),doc?.action||'contact_carrier');
  const roles=selectField(grid,'Quyền đọc',[['both','Vận hành và chỉ xem'],['operator','Chỉ nhân viên vận hành']],doc?.allowed_roles.includes('viewer')?'both':'operator');
  const from=field(grid,'Có hiệu lực từ (ISO, gồm múi giờ)',doc?.effective_from||new Date().toISOString());
  const until=field(grid,'Hết hiệu lực (để trống nếu chưa có)',doc?.effective_to||'');
  form.append(grid);const text=field(form,'Nội dung quy trình',doc?.text||'','textarea');text.rows=9;text.maxLength=10000;
  for(const input of [title,scope,from,text])input.required=true;title.maxLength=160;scope.pattern='[a-zA-Z0-9_-]{1,64}';
  const canEdit=state.user.role==='operator'&&(!doc||doc.kind==='procedure');for(const input of form.querySelectorAll('input,select,textarea'))input.disabled=!canEdit;
  if(canEdit){const file=field(form,'Nạp nội dung từ PDF có chữ / TXT / Markdown','','file');file.accept='.pdf,.txt,.md,application/pdf,text/plain,text/markdown';file.onchange=()=>run(async()=>{
    const f=file.files[0];if(!f)return;
    if(f.name.toLowerCase().endsWith('.pdf')){
      if(f.size>3*1024*1024)throw new Error('PDF vượt giới hạn 3 MiB. Hãy chia nhỏ tài liệu.');
      message('Đang trích xuất PDF; chưa lưu thành quy trình…');
      const encoded=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(new Error('Không đọc được tệp.'));reader.readAsDataURL(f);});
      const preview=await api('/documents/extract-pdf',{content_base64:encoded});text.value=preview.text;
      message(`Đã đọc ${preview.page_count} trang. ${preview.empty_pages.length?'Có trang không đọc được chữ; kiểm tra hoặc dùng OCR. ':''}Hãy kiểm tra nội dung và bảng trước khi lưu.`);
    }else{
      if(f.size>40000)throw new Error('Tệp quá lớn. Giới hạn nội dung 10.000 ký tự.');const value=await f.text();if(value.length>10000)throw new Error('Nội dung vượt quá 10.000 ký tự.');text.value=value;
    }
    if(!title.value)title.value=f.name.replace(/\.[^.]+$/,'').slice(0,160);
  });const save=node('button',doc?'Lưu phiên bản mới':'Tạo quy trình','primary');form.append(save);}
  form.onsubmit=e=>{e.preventDefault();if(!canEdit)return;run(async()=>{const payload={title:title.value,text:text.value,policy_scope:scope.value,action:action.value,allowed_roles:roles.value==='both'?['operator','viewer']:['operator'],effective_from:from.value,effective_to:until.value||null,expected_version:doc?.version||0};const updated=await api('/documents'+(doc?'/'+doc.id:''),payload,doc?'PUT':'POST');await documentEditor(updated);message(`Đã lưu phiên bản ${updated.version}.`);});};
  panel.append(form,button('← Danh sách quy trình',()=>documents(),'quiet'));
  if(doc){panel.append(button('Xem lịch sử phiên bản',async()=>{const versions=await api('/documents/'+doc.id+'/versions');const history=node('div');for(const v of versions){const d=node('details',undefined,'source');d.append(node('summary',`Phiên bản ${v.version}`),node('blockquote',v.text));history.append(d);}panel.append(history);}));}
  $('content').append(panel);
}
$('login-form').onsubmit=e=>{e.preventDefault();run(async()=>{await api('/demo/login',{actor_id:$('actor').value});state.user=await api('/me');showSession();await navigate('queue');});};
$('logout').onclick=()=>run(async()=>{guardDraft();await api('/logout',{});state.user=null;showSession();message('Đã đăng xuất.');});
window.addEventListener('beforeunload',event=>{if(draftIsDirty(state.draftEditor)){event.preventDefault();event.returnValue='';}});
document.querySelectorAll('[data-page]').forEach(b=>b.onclick=()=>run(()=>navigate(b.dataset.page)));
run(async()=>{const health=await fetch('/health').then(r=>r.json());$('mode').textContent=health.mode==='openai'?'OpenAI · cần kiểm tra kết quả':'Offline · không gọi GPT';try{state.user=await api('/me');}catch(e){if(!e.message.includes('Session'))message(e.message,true);}showSession();if(state.user)await navigate('queue');});
