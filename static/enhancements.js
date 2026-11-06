/* Northstar AI enhanced consultation screens. Loaded after app.js. */
const ext = id => document.getElementById(id);
const extSafe = x => String(x ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let completion = [false,false,false,false,false,false];
let computedCost = {};
const milestones = [
 ["Diagnose","Define one measurable business problem"],
 ["Check readiness","Review data, permissions and existing subscriptions"],
 ["Select a tool","Confirm real costs, feature limits and integrations"],
 ["Pilot","Test with sample data and human oversight"],
 ["Launch","Approve the configuration and monitor exceptions"],
 ["Measure","Compare actual results against the original baseline"]
];
function extReadiness(d){
 const a=d.readiness, audit=d.software_audit;
 ext('readiness-content').innerHTML=`<div class="metric-line"><div><h3>${extSafe(a.status)}</h3><p>${extSafe(a.note)}</p></div><strong class="readiness-number">${Number(a.score)}/100</strong></div>
 <div class="meter"><div style="width:${Math.max(0,Math.min(100,a.score))}%"></div></div>
 <div class="beforeafter"><div><h3>What you have</h3><ul>${a.positives.map(x=>'<li>'+extSafe(x)+'</li>').join('')}</ul></div>
 <div><h3>What's next</h3><ul>${[...a.blockers,...a.next_steps].map(x=>'<li>'+extSafe(x)+'</li>').join('')}</ul></div></div>`;
 ext('software-audit-content').innerHTML=`<div class="panel"><div class="section-top"><h3>What about ${extSafe(audit.software)}?</h3><span class="tag tag-soft">Reuse first</span></div>
 <div class="audit-grid"><article><b>Keep what works</b><p>${extSafe(audit.existing_value)}</p></article>
 <article><b>Check existing features</b><p>${extSafe(audit.try_first)}</p></article>
 <article><b>Consider next</b><p>${extSafe(audit.future)}</p></article></div><p class="fineprint">${extSafe(audit.note)}</p></div>`;
}
function extReviews(d){
 const el=ext('review-tool');
 el.innerHTML=d.tools.map(t=>`<option value="${extSafe(t.id)}">${extSafe(t.name)}</option>`).join('');
 function render(){
  const t=d.tools.find(x=>x.id===el.value)||d.tools[0];if(!t)return;
  const e=t.review_evidence;
  ext('review-content').innerHTML=`<div class="review-item"><b>${extSafe(t.name)} — look for a similar business</b>
  <p>Look for company size, industry, setup time, reported limitations and outcomes. Ratings here are not invented.</p>
  <a href="${extSafe(e.search_url)}" target="_blank" rel="noopener noreferrer">Search for independent reviews on ${extSafe(e.directory_name)} ↗</a>
  <p class="fineprint">${extSafe(e.note)}</p></div>`;
 }
 el.onchange=render; render();
}
function extArchitecture(d){
 const b=d.blueprint, process=(items)=>items.map((x,i)=>`<div class="process-tile"><span>${i+1}</span>${extSafe(x)}</div>`).join('');
 ext('workflow-before').innerHTML=process(b.before);
 ext('workflow-after').innerHTML=process(b.after);
 ext('architecture-flow').innerHTML=b.nodes.map((n,i)=>`<button type="button" class="flow-tile" data-node="${i}"><span>STEP ${i+1}</span><b>${extSafe(n.title)}</b></button>`).join('');
 const nodes=ext('architecture-flow').querySelectorAll('[data-node]');
 function select(i){nodes.forEach((n,j)=>n.classList.toggle('selected',j===i));let n=b.nodes[i];ext('architecture-detail').innerHTML=`<b>${extSafe(n.title)}</b><p>${extSafe(n.detail)}</p><small>No live connection is implied.</small>`;}
 nodes.forEach((n,i)=>n.addEventListener('click',()=>select(i))); select(0);
}
function extStorageKey(){return 'northstar-v2-progress:'+String(profile.business||'demo').toLowerCase()+':'+String(result?.problem||'unknown');}
function extLoadProgress(){
 try{const value=JSON.parse(localStorage.getItem(extStorageKey())||'[]');completion=milestones.map((x,i)=>value[i]===true);}catch{completion=milestones.map(()=>false);}
}
function extTrack(){
 let container=ext('roadmap-content');if(!container||!result)return;
 const count=completion.filter(Boolean).length;
 container.innerHTML=`<h3>Follow your progress like a package delivery</h3><div class="progress-wrap"><div style="width:${100*count/6}%"></div></div>
 <p class="progress-count">${count} of 6 steps marked complete · Manually tracked, not a live vendor status</p>
 ${milestones.map(([title,detail],i)=>`<label class="tracked-item"><input type="checkbox" data-milestone="${i}" ${completion[i]?'checked':''}>
 <span class="tracked-number">${completion[i]?'✓':i+1}</span><span><b>${extSafe(title)}</b><small>${extSafe(detail)}</small></span></label>`).join('')}
 <p class="fineprint">You can return to this browser to continue where you left off.</p>`;
 container.querySelectorAll('[data-milestone]').forEach(box=>box.addEventListener('change',()=>{
  completion[Number(box.dataset.milestone)]=box.checked;
  try{localStorage.setItem(extStorageKey(),JSON.stringify(completion));}catch{}
  extTrack(); extPassport();
 }));
}
function extComputeCost(){
 const v=id=>Math.max(0,Number(ext(id)?.value)||0);
 const monthlyCost=v('cost')+v('extra-fees'), setup=v('setup-fees'), value=v('hours')*v('hourly')+v('cash-benefits');
 const monthlyNet=value-monthlyCost, yearlyNet=12*monthlyNet-setup;
 computedCost={monthlyCost,setup,value,monthlyNet,yearlyNet,payback:monthlyNet>0?setup/monthlyNet:null};
 ext('roi-result').textContent=(monthlyNet<0?'-':'')+'$'+Math.abs(monthlyNet).toLocaleString(undefined,{maximumFractionDigits:2});
 ext('roi-context').textContent=`First year: ${yearlyNet.toLocaleString('en-US',{style:'currency',currency:'USD',maximumFractionDigits:0})} · Setup payback: ${computedCost.payback===null?'not reached':computedCost.payback.toFixed(1)+' months'} (assumptions only; labor hours may not be cash savings).`;
 extPassport();
}
function extPassportData(){
 if(!result)return null;
 return {generated_at:new Date().toISOString(),business:profile.business,industry:profile.industry,challenge:result.problem,
  current_software:profile.software,monthly_budget:profile.budget,readiness:result.readiness,
  existing_software_audit:result.software_audit,proposed_architecture:result.blueprint,
  candidate_tools:result.tools.map(t=>({name:t.name,illustrative_match:t.score,pricing:t.pricing,vendor:t.site,reasons:t.reasons,review_search:t.review_evidence?.search_url})),
  cost_assumptions:computedCost,implementation:milestones.map(([name,detail],i)=>({name,detail,completed:completion[i]})),
  limitations:'Research prototype. Vendor prices and integrations must be independently verified. No third-party systems deployed.'};
}
function extPassport(){
 if(!result)return;
 const data=extPassportData(),top=result.tools[0],done=completion.filter(Boolean).length;
 ext('passport-content').innerHTML=`<div class="passport-head"><div><div class="eyebrow">NORTHSTAR · AI DECISION PASSPORT</div><h2>${extSafe(profile.business)}</h2><p>${extSafe(result.blueprint.goal)}</p></div><span class="tag tag-soft">Decision draft</span></div>
 <div class="passport-grid"><div><small>AI readiness</small><strong>${data.readiness.score}/100</strong></div><div><small>Top candidate</small><strong>${extSafe(top.name)}</strong></div>
 <div><small>Estimated monthly net</small><strong>${(computedCost.monthlyNet||0).toLocaleString('en-US',{style:'currency',currency:'USD',maximumFractionDigits:0})}</strong></div>
 <div><small>Implementation</small><strong>${done}/6</strong></div></div>
 <h3>Why consider this?</h3><ul>${(top.reasons||[]).map(x=>'<li>'+extSafe(x)+'</li>').join('')}</ul>
 <h3>What should you check first?</h3><p>${extSafe(data.existing_software_audit.try_first)}</p>
 <h3>Known blockers and assumptions</h3><ul>${[...data.readiness.blockers,'Vendor prices, plan eligibility, data permissions and integrations require confirmation.'].map(x=>'<li>'+extSafe(x)+'</li>').join('')}</ul>
 <h3>Measure success</h3><p>${extSafe(result.blueprint.metric)}</p><p class="fineprint">${extSafe(data.limitations)}</p>`;
}
function extDownload(){
 const payload=extPassportData();if(!payload){showPane('profile');return;}
 const blob=new Blob([JSON.stringify(payload,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob);
 const a=document.createElement('a');a.href=url;a.download='northstar-decision-passport.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
const originalShowResults=showResults;
showResults=function(data){originalShowResults(data);extReadiness(data);extReviews(data);extArchitecture(data);extLoadProgress();extComputeCost();extPassport();};
const originalShowRoadmap=showRoadmap;
showRoadmap=function(data){originalShowRoadmap(data);extTrack();};
const lab=ext('cost')?.parentElement?.parentElement;
if(lab){
 const e=document.createElement('div');e.className='extra-costs';e.innerHTML=`<label>Other monthly costs ($)<input type="number" id="extra-fees" value="15" min="0"></label>
 <label>One-time setup ($)<input type="number" id="setup-fees" value="100" min="0"></label>
 <label>Additional monthly cash benefit ($)<input type="number" id="cash-benefits" value="0" min="0"></label>`;
 ext('cost').parentElement.after(e);
}
['cost','hours','hourly','extra-fees','setup-fees','cash-benefits'].forEach(id=>ext(id)?.addEventListener('input',extComputeCost));
ext('passport-download').addEventListener('click',extDownload);
ext('passport-print').addEventListener('click',()=>{if(!result){showPane('profile');return;}showPane('passport');setTimeout(()=>window.print(),200);});
ext('review-form').addEventListener('submit',async event=>{
 event.preventDefault();
 const data=Object.fromEntries(new FormData(event.currentTarget));
 ext('review-feedback').textContent='Saving for moderation…';
 try{const res=await fetch('/api/reviews/submit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
 const body=await res.json();if(!res.ok)throw Error(body.error||'Unable to submit');
 ext('review-feedback').textContent=body.message;event.currentTarget.reset();}
 catch(error){ext('review-feedback').textContent=error.message;}
});
