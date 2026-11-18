
document.addEventListener('DOMContentLoaded',()=>{
 if(!document.getElementById('pv-v2'))return;
 const root=document.getElementById('pv-v2'),q=id=>root.querySelector('#'+id);
 const keys=['patient','reporter','drug','reaction'];
 const titles={patient:'Пациент',reporter:'Репортёр',drug:'Препарат',reaction:'Реакция / особая ситуация'};
 const kinds=['Применение во время беременности','Применение при грудном вскармливании','Передозировка','Ошибка применения','Отсутствие эффективности','Применение вне инструкции','Неправильное применение','Злоупотребление','Профессиональное воздействие','Другое'];
 const criteria=['Смерть','Угроза жизни','Госпитализация или её продление','Стойкая или значительная нетрудоспособность / инвалидизация','Врождённая аномалия / порок развития','Другое клинически значимое событие'];
 const medicines=Object.fromEntries(JSON.parse(q('pv-product-data').textContent).map(p=>[p.id,p.name]));
 const directory=JSON.parse(q('pv-directory-data').textContent);
 Object.entries(medicines).forEach(([id,name])=>q('pv-med-company').append(new Option(name,id)));
 const form=root.closest('form');

 const copy=x=>JSON.parse(JSON.stringify(x));
 const uid=()=>Date.now().toString(36)+'-'+Math.random().toString(36).slice(2,9);
 const emptyBinding=()=>({mode:'',personId:'',role:''});
 const emptyPerson=()=>({id:uid(),name:'',country:'',phone:'',email:'',saved:false});
 const emptyMed=()=>({id:uid(),companyId:'',reported:''});
 const defaults=()=>JSON.parse(q('pv-initial-state').textContent);
 let state=defaults(),panel='',previousFocus=null,returnTo='',contactKey='',contactIndex=0,contactBinding=null,peopleDraft=[],extraDraft=[],personEditingId='',medDraft=[],medIndex=0;
 const hasText=v=>typeof v==='string'&&v.trim().length>0;
 const personHasData=p=>!!p&&['name','country','phone','email'].some(k=>hasText(p[k]));
 function projectPeople(){
  if(!form.matches('[data-message-intake]'))return;
  function person(binding,seen=false){if(binding.mode==='same'&&!seen)return person(state.contacts.sender,true);if(binding.mode==='patient')return {name:state.patient.initials,phone:state.patient.phone,email:state.patient.email};return ['new','existing','local'].includes(binding.mode)?state.people.find(p=>p.id===binding.personId)||{}:{};}
  [['sender','sender'],['reporter','primary']].forEach(([role,key])=>{const binding=state.contacts[key],p=person(binding);['name','phone','email'].forEach(k=>{const field=form.elements.namedItem(role+'_'+k);if(field)field.value=p[k]||'';});const select=form.elements.namedItem(role+'_contact_id');if(select){const linked=binding.mode==='existing'||(binding.mode==='same'&&state.contacts.sender.mode==='existing');const id=linked?(p.id||''):'';if(id&&!Array.from(select.options).some(o=>o.value===id))select.append(new Option(p.name||p.email||p.phone||'Контакт',id));select.value=id;}});
  form.elements.namedItem('reporter_same_as_sender').checked=state.contacts.primary.mode==='same';
  form.elements.namedItem('reporter_unknown').checked=state.contacts.primary.mode==='unknown';
  const save=form.elements.namedItem('save_sender_contact');if(save)save.checked=false;
 }
 function persist(){projectPeople();q('pv-card-data').value=JSON.stringify(state);q('pv-card-dirty').value='true';notify();}
 function notify(){form.dispatchEvent(new Event('input',{bubbles:true}));}
 function resolveContact(binding,seen=false){
  if(!binding)return {status:'empty',summary:'Не заполнен'};
  if(binding.mode==='same')return seen?{status:'empty',summary:'Контакт не выбран'}:resolveContact(state.contacts.sender,true);
  if(binding.mode==='patient'){const p=state.patient;return {status:'filled',summary:'Пациент'+(p.initials?' · '+p.initials:'')};}
  if(binding.mode==='unknown')return {status:'unknown',summary:'Неизвестно'};
  const p=state.people.find(x=>x.id===binding.personId)||directory.find(x=>x.id===binding.personId);return {status:(binding.mode==='existing'&&binding.personId)||personHasData(p)?'filled':'empty',summary:p?(p.name||p.email||p.phone||p.country||'Контакт'):'Не заполнен'};
 }
 function contactSummary(b){if(b.mode==='same')return 'Тот же контакт · '+resolveContact(b).summary;return resolveContact(b).summary;}
 function statuses(){
  const contacts=[state.contacts.sender,state.contacts.primary,...state.contacts.additional].map(x=>resolveContact(x).status);
  return {
   patient:state.unknown.patient?'unknown':Object.values(state.patient).some(hasText)?'filled':'empty',
   reporter:state.unknown.reporter?'unknown':contacts.includes('filled')?'filled':contacts.includes('unknown')?'unknown':'empty',
   drug:state.unknown.drug?'unknown':state.medications.some(x=>x.companyId||hasText(x.reported))?'filled':'empty',
   reaction:state.unknown.reaction?'unknown':hasText(state.description)||(state.special&&state.specials.length)||state.criteria.length||state.ae||state.special?'filled':'empty'
  };
 }
 const stateText=s=>s==='filled'?'Есть сведения':s==='unknown'?'Неизвестно':'Не заполнено';
 const mark=s=>s==='filled'?'✓':s==='unknown'?'?':'—';
 function refreshStatus(){
  const all=statuses(),count=keys.filter(k=>all[k]==='filled').length;
  keys.forEach(k=>{q('pv-section-'+k).dataset.state=all[k];const badge=q('pv-mark-'+k);badge.dataset.state=all[k];badge.textContent=mark(all[k]);badge.setAttribute('aria-label',stateText(all[k]));badge.setAttribute('data-tooltip',stateText(all[k]));q('pv-unknown-'+k).checked=state.unknown[k];const status=q('pv-validity-state-'+k),checkbox=q('pv-validity-unknown-'+k);if(status){status.dataset.state=all[k];status.textContent=mark(all[k])+' '+stateText(all[k]);checkbox.checked=state.unknown[k];}});
  q('pv-validity-open').textContent='Оценка валидности · '+count+'/4';q('pv-validity-total').textContent='Есть сведения: '+count+' из 4';
 }
 function renderContactSummaries(){
  ['sender','primary'].forEach(k=>{const b=state.contacts[k],summary=contactSummary(b);q('pv-'+k+'-summary').textContent=summary;q('pv-'+k+'-summary').setAttribute('data-tooltip',summary);const button=root.querySelector('[data-contact="'+k+'"]');button.textContent=b.mode?'Изменить':'Заполнить';button.setAttribute('data-tooltip',summary);});q('pv-extra-open').hidden=!state.contacts.additional.length;q('pv-extra-open').textContent='Ещё контакты: '+state.contacts.additional.length+' ↗';
 }
 function renderMedications(){
  const target=q('pv-med-rows'),scroll=q('pv-med-scroll'),previousScroll=scroll.scrollTop;target.replaceChildren();
  state.medications.forEach((med,index)=>{
   const row=document.createElement('div');row.className='pv-med-row';row.dataset.medId=med.id;row.setAttribute('role','group');row.setAttribute('aria-label','Препарат '+(index+1));
   const attach=document.createElement('button');attach.type='button';attach.className='pv-select-button';attach.id=index===0?'pv-drug-attach':'pv-drug-attach-'+med.id;attach.dataset.attachMed=med.id;const text=document.createElement('span');text.textContent=medicines[med.companyId]||'Прикрепить ЛП';const arrow=document.createElement('span');arrow.textContent='⌄';arrow.setAttribute('aria-hidden','true');attach.append(text,arrow);attach.setAttribute('aria-label','Прикрепить ЛП — препарат '+(index+1)+(med.companyId?': '+medicines[med.companyId]:''));if(med.companyId)attach.setAttribute('data-tooltip',medicines[med.companyId]);attach.onclick=()=>openMed(state.medications.findIndex(m=>m.id===med.id));
   const input=document.createElement('input');input.className='pv-field';input.id=index===0?'pv-drug-reported':'pv-drug-reported-'+med.id;input.dataset.reportedMed=med.id;input.setAttribute('aria-label','Как указан в сообщении — препарат '+(index+1));input.placeholder='Название / формулировка';input.value=med.reported;input.oninput=()=>{med.reported=input.value;refreshStatus();persist();};input.onchange=persist;
   const remove=document.createElement('button');remove.type='button';remove.className='pv-button pv-med-delete';remove.textContent='×';remove.dataset.removeMed=med.id;remove.setAttribute('aria-label','Удалить препарат '+(index+1));remove.setAttribute('data-tooltip','Удалить препарат');remove.onclick=()=>{const position=state.medications.findIndex(m=>m.id===med.id);state.medications.splice(position,1);if(!state.medications.length)state.medications=[emptyMed()];renderMedications();refreshStatus();persist();const inputs=q('pv-med-rows').querySelectorAll('input');inputs[Math.min(position,inputs.length-1)].focus({preventScroll:true});};
   row.append(attach,input,remove);target.append(row);
  });scroll.scrollTop=previousScroll;
 }
 function render(){
  ['initials','age','sex','phone','email'].forEach(k=>q('pv-patient-'+k).value=state.patient[k]);q('pv-patient-other').textContent='Подробнее';renderContactSummaries();renderMedications();q('pv-ae').checked=state.ae;q('pv-special').checked=state.special;q('pv-special-summary').textContent=state.specials.length?'Ситуации: '+state.specials.length:'Выбрать ситуации';q('pv-special-button').title=state.specials.length?state.specials.join('; '):'Выбрать особые ситуации';q('pv-description').value=state.description;q('pv-seriousness').value=state.serious;q('pv-criteria-summary').textContent=state.criteria.length?'Критерии серьёзности: '+state.criteria.length+' · Посмотреть / изменить':'Критерии серьёзности · Выбрать…';refreshStatus();
 }
 function checkList(id,values,selected,prefix){const target=q(id);target.replaceChildren();values.forEach((v,i)=>{const label=document.createElement('label');label.className='pv-check';const input=document.createElement('input');input.type='checkbox';input.id=prefix+i;input.value=v;input.checked=selected.includes(v);label.append(input,document.createTextNode(v));target.append(label);});}
 const checked=id=>Array.from(q(id).querySelectorAll('input:checked')).map(x=>x.value);
 function createValidity(){q('pv-validity-rows').replaceChildren();keys.forEach(k=>{const row=document.createElement('div');row.className='pv-validity-row';const name=document.createElement('span');name.textContent=titles[k];const status=document.createElement('span');status.className='pv-state';status.id='pv-validity-state-'+k;const label=document.createElement('label');label.className='pv-check';const checkbox=document.createElement('input');checkbox.type='checkbox';checkbox.id='pv-validity-unknown-'+k;checkbox.setAttribute('aria-label',titles[k]+': неизвестно');checkbox.onchange=()=>{state.unknown[k]=checkbox.checked;renderContactSummaries();refreshStatus();persist();};label.append(checkbox,document.createTextNode('Неизвестно'));row.append(name,status,label);q('pv-validity-rows').append(row);});refreshStatus();}
 function setInert(value){Array.from(root.children).filter(x=>x.id!=='pv-overlay').forEach(x=>x.inert=value);}
 function close(force=false){if(returnTo&&!force){const target=returnTo;returnTo='';open(target);return;}returnTo='';q('pv-overlay').hidden=true;setInert(false);if(previousFocus?.isConnected)previousFocus.focus();}
 function open(name){
  if(q('pv-overlay').hidden)previousFocus=document.activeElement;
  if(name==='icsr'){form.requestSubmit(form.querySelector('[data-pv-handoff]')||form.querySelector('[type=submit]'));return;}
  if(name==='safety'&&panel==='drugs'&&!q('pv-overlay').hidden){readMed();returnTo='drugs';}
  panel=name;const textPanel=name==='patient'||name==='description';['special','drugs','text','criteria','contact','source','drugtip','safety','icsr','validity'].forEach(k=>q('pv-panel-'+k).hidden=k!==(textPanel?'text':name));q('pv-overlay').hidden=false;setInert(true);
  const info=['source','drugtip','safety','icsr','validity'].includes(name);q('pv-cancel').hidden=info;q('pv-apply').textContent=info?'Закрыть':'Применить';q('pv-contact-remove').hidden=true;q('pv-med-remove').hidden=true;
  const headings={patient:'Подробнее — сведения о пациенте',description:'Описание',special:'Какие особые ситуации?',drugs:'Препарат',criteria:'Критерии серьёзности',contact:'Контактное лицо',source:'Исходное входящее сообщение',drugtip:'Сведения о других препаратах',safety:'Информация по безопасности',icsr:'Подробная карточка ИСНР',validity:'Оценка валидности сообщения'};q('pv-dialog-title').textContent=headings[name];
  if(textPanel){q('pv-text-label').textContent=name==='patient'?'Дополнительные сведения о пациенте':'Описание';q('pv-expanded-text').value=name==='patient'?state.patient.other:state.description;}
  if(name==='special'){checkList('pv-special-options',kinds,state.specials,'pv-kind-');q('pv-other').value=state.other;q('pv-other-wrap').hidden=!state.specials.includes('Другое');}
  if(name==='criteria')checkList('pv-criteria-options',criteria,state.criteria,'pv-criterion-');
  if(name==='validity')createValidity();if(name==='drugs')fillMed();q('pv-close').focus();
 }
 function getDraftPerson(){return peopleDraft.find(p=>p.id===personEditingId);}
 function flushPerson(){if(!['new','existing','local'].includes(contactBinding?.mode))return;const p=getDraftPerson();if(!p)return;['name','country','phone','email'].forEach(k=>p[k]=q('pv-contact-'+k).value);if(['new','local'].includes(contactBinding.mode))p.saved=q('pv-save-contact').checked;}
 function setDraftPerson(id){personEditingId=id;const p=getDraftPerson()||emptyPerson();['name','country','phone','email'].forEach(k=>q('pv-contact-'+k).value=p[k]||'');q('pv-save-contact').checked=!!p.saved;}
 function fillDirectory(){
 const target=q('pv-contact-existing');target.replaceChildren(new Option('Выберите контакт',''));
 const people=new Map(directory.map(p=>[p.id,p]));peopleDraft.filter(p=>p.saved).forEach(p=>people.set(p.id,p));
 people.forEach(p=>target.append(new Option(p.name||p.email||p.phone||p.country,p.id)));target.value=contactBinding.personId||'';
}
 function updateContactVisibility(){const mode=contactBinding.mode;q('pv-contact-fields').hidden=!['new','existing','local'].includes(mode)||(['existing','local'].includes(mode)&&!contactBinding.personId);q('pv-save-contact-wrap').hidden=!['new','local'].includes(mode);q('pv-contact-existing-wrap').hidden=!['existing','local'].includes(mode);q('pv-contact-linked').hidden=!['same','patient'].includes(mode);q('pv-contact-linked-summary').textContent=mode==='patient'?'Используются данные пациента':mode==='same'?'Используется контакт, от которого получено сообщение':'';q('pv-contact-role-wrap').hidden=contactKey!=='additional';q('pv-contact-error').hidden=true;}
 function fillContact(){
  const options=contactKey==='primary'?[['same','Тот же контакт'],['existing','Выбрать из списка контактных лиц'],['new','Ввести новый контакт'],['patient','Пациент'],['unknown','Неизвестно']]:[['patient','Пациент'],['existing','Выбрать из списка контактных лиц'],['new','Ввести новый контакт'],['unknown','Неизвестно']];
  q('pv-contact-mode').replaceChildren(new Option('Выберите вариант',''));options.splice(options.length-1,0,['local','Выбрать контакт этого сообщения']);options.forEach(([value,label])=>q('pv-contact-mode').append(new Option(label,value)));q('pv-contact-mode').value=contactBinding.mode;fillDirectory();if(contactBinding.mode==='local'){q('pv-contact-existing').replaceChildren(new Option('Выберите контакт',''));peopleDraft.filter(p=>!p.saved).forEach(p=>q('pv-contact-existing').append(new Option(p.name||p.email||p.phone||p.country||'Контакт',p.id)));q('pv-contact-existing').value=contactBinding.personId;}setDraftPerson(contactBinding.personId);q('pv-contact-role').value=contactBinding.role||'';updateContactVisibility();q('pv-contact-remove').hidden=contactKey!=='additional';q('pv-dialog-title').textContent=contactKey==='sender'?'От кого получено сообщение':contactKey==='primary'?'Первичный источник':'Дополнительный контакт';
 }
 function fillExtraPicker(){q('pv-contact-picker').replaceChildren();extraDraft.forEach((b,i)=>{const p=peopleDraft.find(p=>p.id===b.personId);q('pv-contact-picker').append(new Option((i+1)+'. '+(b.role?b.role+' · ':'')+(p?.name||p?.email||b.mode==='patient'&&'Пациент'||'Контакт'),String(i)));});q('pv-contact-picker').value=String(contactIndex);}
 function readBinding(){flushPerson();contactBinding.role=q('pv-contact-role').value;return copy(contactBinding);}
 function openContact(key,index=0){contactKey=key;contactIndex=index;peopleDraft=copy(state.people);extraDraft=copy(state.contacts.additional);if(key==='additional'&&index===extraDraft.length)extraDraft.push(emptyBinding());contactBinding=copy(key==='additional'?extraDraft[index]:state.contacts[key]);open('contact');q('pv-contact-picker-wrap').hidden=key!=='additional';if(key==='additional')fillExtraPicker();fillContact();}
 function commitContact(){const binding=readBinding(),person=getDraftPerson();if(['existing','local'].includes(binding.mode)&&!binding.personId){q('pv-contact-error').textContent='Выберите контакт из списка контактных лиц.';q('pv-contact-error').hidden=false;return false;}if(binding.mode==='new'&&person?.saved&&!personHasData(person)){q('pv-contact-error').textContent='Заполните известные сведения о контактном лице.';q('pv-contact-error').hidden=false;return false;}if(contactKey==='additional'){extraDraft[contactIndex]=binding;state.contacts.additional=extraDraft;}else state.contacts[contactKey]=binding;if(contactKey==='sender'&&binding.mode==='patient'&&!state.contacts.primary.mode)state.contacts.primary={...emptyBinding(),mode:'patient'};state.patientSourceRule=1;const used=[state.contacts.sender,state.contacts.primary,...state.contacts.additional].map(b=>b.personId);state.people=peopleDraft.filter(p=>p.saved||used.includes(p.id));return true;}
 q('pv-contact-mode').onchange=e=>{flushPerson();contactBinding.mode=e.target.value;contactBinding.personId='';if(contactBinding.mode==='new'){const p=emptyPerson();peopleDraft.push(p);contactBinding.personId=p.id;setDraftPerson(p.id);}else setDraftPerson('');fillDirectory();if(contactBinding.mode==='local'){q('pv-contact-existing').replaceChildren(new Option('Выберите контакт',''));peopleDraft.filter(p=>!p.saved).forEach(p=>q('pv-contact-existing').append(new Option(p.name||p.email||p.phone||p.country||'Контакт',p.id)));}updateContactVisibility();};
 q('pv-contact-existing').onchange=e=>{flushPerson();contactBinding.personId=e.target.value;if(!peopleDraft.some(p=>p.id===e.target.value)){const found=directory.find(p=>p.id===e.target.value);if(found)peopleDraft.push(copy(found));}setDraftPerson(e.target.value);updateContactVisibility();};
 q('pv-contact-picker').onchange=e=>{extraDraft[contactIndex]=readBinding();contactIndex=Number(e.target.value);contactBinding=copy(extraDraft[contactIndex]);fillContact();};
 q('pv-contact-linked-edit').onclick=()=>{const mode=contactBinding.mode;if(!commitContact())return;render();persist();close(true);if(mode==='same')openContact('sender');else q('pv-patient-initials').focus();};
 q('pv-contact-remove').onclick=()=>{extraDraft.splice(contactIndex,1);state.contacts.additional=extraDraft;render();persist();close(true);};
 function medLabel(m){return medicines[m.companyId]||m.reported||'Без названия';}
 function readMed(){if(!medDraft[medIndex])return;medDraft[medIndex].companyId=q('pv-med-company').value;medDraft[medIndex].reported=q('pv-med-reported').value;}
 function fillMed(){q('pv-med-picker').replaceChildren();medDraft.forEach((m,i)=>q('pv-med-picker').append(new Option((i+1)+'. '+medLabel(m),String(i))));q('pv-med-picker').value=String(medIndex);q('pv-med-company').value=medDraft[medIndex].companyId;q('pv-med-reported').value=medDraft[medIndex].reported;q('pv-med-remove').hidden=false;}
 function openMed(index){if(index<0)return;medDraft=copy(state.medications);medIndex=index;open('drugs');q('pv-med-picker-wrap').hidden=true;q('pv-dialog-title').textContent='Препарат '+(index+1);}
 q('pv-med-picker').onchange=e=>{readMed();medIndex=Number(e.target.value);fillMed();};
 q('pv-med-remove').onclick=()=>{medDraft.splice(medIndex,1);state.medications=medDraft.length?medDraft:[emptyMed()];render();persist();close(true);};
 root.querySelectorAll('[data-open]').forEach(b=>b.onclick=()=>open(b.dataset.open));root.querySelectorAll('[data-contact]').forEach(b=>b.onclick=()=>openContact(b.dataset.contact));q('pv-contact-add').onclick=()=>openContact('additional',state.contacts.additional.length);q('pv-extra-open').onclick=()=>openContact('additional');q('pv-drug-add').onclick=()=>{state.medications.push(emptyMed());renderMedications();refreshStatus();persist();q('pv-med-scroll').scrollTop=q('pv-med-scroll').scrollHeight;q('pv-med-rows').lastElementChild.querySelector('input').focus({preventScroll:true});};
 q('pv-close').onclick=()=>close();q('pv-cancel').onclick=()=>close(true);q('pv-overlay').onclick=e=>{if(e.target===q('pv-overlay'))close();};q('pv-special-options').onchange=()=>q('pv-other-wrap').hidden=!checked('pv-special-options').includes('Другое');
 q('pv-apply').onclick=()=>{
  if(panel==='safety'&&returnTo){close();return;}
  if(panel==='contact'&&!commitContact())return;
  if(panel==='drugs'){readMed();state.medications=medDraft;if(!state.medications.length)state.medications=[emptyMed()];}
  if(panel==='special'){state.specials=checked('pv-special-options');state.special=state.specials.length>0;state.other=q('pv-other').value;state.touched.reaction=true;}
  if(panel==='criteria'){state.criteria=checked('pv-criteria-options');if(state.criteria.length){state.serious='yes';state.touched.serious=true;}}
  if(panel==='patient')state.patient.other=q('pv-expanded-text').value;if(panel==='description')state.description=q('pv-expanded-text').value;
  render();persist();close(true);
 };
 ['initials','age','sex','phone','email'].forEach(k=>{q('pv-patient-'+k).oninput=e=>{state.patient[k]=e.target.value;renderContactSummaries();refreshStatus();persist();};q('pv-patient-'+k).onchange=e=>{state.patient[k]=e.target.value;renderContactSummaries();refreshStatus();persist();};});
 root.querySelectorAll('[data-unknown]').forEach(input=>input.onchange=()=>{state.unknown[input.dataset.unknown]=input.checked;renderContactSummaries();refreshStatus();persist();});
 [['pv-ae','ae'],['pv-special','special']].forEach(([id,key])=>q(id).onchange=e=>{state[key]=e.target.checked;state.touched.reaction=true;render();persist();});
 q('pv-description').oninput=e=>{state.description=e.target.value;refreshStatus();persist();};q('pv-description').onchange=persist;
 q('pv-seriousness').onchange=e=>{state.serious=e.target.value;state.touched.serious=true;if(state.serious==='no')state.criteria=[];render();persist();};

 root.addEventListener('keydown',e=>{if(q('pv-overlay').hidden)return;if(e.key==='Escape'){e.preventDefault();close();}if(e.key==='Tab'){const controls=Array.from(q('pv-overlay').querySelectorAll('button,input,textarea,select,a[href]')).filter(x=>!x.disabled&&x.getClientRects().length),first=controls[0],last=controls[controls.length-1];if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}}});
 function importCommon(event){
  if(!form.matches('[data-message-intake]'))return;
  const name=event.target.name||'';
  const role=name.startsWith('sender_')?'sender':name.startsWith('reporter_')?'reporter':null;if(!role)return;
  const key=role==='sender'?'sender':'primary';let binding=state.contacts[key];
  if(name==='reporter_same_as_sender'||name==='reporter_unknown'){
   binding.mode=form.elements.namedItem('reporter_same_as_sender').checked?'same':form.elements.namedItem('reporter_unknown').checked?'unknown':'';
  }else if(name===role+'_contact_id'){
   const id=event.target.value;binding.mode=id&&id!=='__new__'?'existing':id==='__new__'?'new':'';binding.personId=binding.mode==='existing'?id:uid();
   if(binding.mode&&!state.people.some(p=>p.id===binding.personId)){const p=directory.find(p=>p.id===id);state.people.push(p?copy(p):{...emptyPerson(),id:binding.personId});}
  }else if(['name','phone','email','country_code'].some(k=>name===role+'_'+k)&&!['patient','same','unknown'].includes(binding.mode)){
   let p=state.people.find(p=>p.id===binding.personId);if(!p){p=emptyPerson();state.people.push(p);binding.mode='new';binding.personId=p.id;}
   ['name','phone','email'].forEach(k=>p[k]=form.elements.namedItem(role+'_'+k).value);
   if(name==='reporter_country_code')p.country=event.target.value;
  }else return;
  renderContactSummaries();refreshStatus();persist();
 }
 form.addEventListener('input',importCommon);form.addEventListener('change',importCommon);
 render();projectPeople();
});
