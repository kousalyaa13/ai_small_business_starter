document.addEventListener('DOMContentLoaded',()=>{const menu=document.getElementById('menu-toggle'),nav=document.getElementById('primary-nav');if(menu&&nav){menu.addEventListener('click',()=>{const open=nav.classList.toggle('open');menu.setAttribute('aria-expanded',String(open));menu.setAttribute('aria-label',open?'Close navigation':'Open navigation')});nav.querySelectorAll('a').forEach(a=>a.addEventListener('click',()=>{nav.classList.remove('open');menu.setAttribute('aria-expanded','false')}))}const picker=document.getElementById('challenge-form');if(picker){picker.addEventListener('submit',e=>{e.preventDefault();const chosen=new FormData(picker).get('challenge');if(chosen)window.location.assign('/consult?challenge='+encodeURIComponent(chosen))})}});
/* Reveal sections as they enter view. Content stays visible if JS or observer fails. */
if('IntersectionObserver' in window && !window.matchMedia('(prefers-reduced-motion: reduce)').matches){
 const sections=Array.from(document.querySelectorAll('.reveal'));
 if(sections.length){
  const observer=new IntersectionObserver((entries)=>{
   entries.forEach(entry=>{if(entry.isIntersecting){entry.target.classList.add('is-visible');observer.unobserve(entry.target)}});
  },{threshold:.08,rootMargin:'0px 0px 60px 0px'});
  sections.forEach(section=>{if(section.getBoundingClientRect().top<window.innerHeight*.95){section.classList.add('is-visible')}else{observer.observe(section)}});
  document.documentElement.classList.add('js-motion');
 }
}
