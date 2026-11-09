const assert = require('node:assert/strict');
const {groupTools, uniqueTasks, billingLabel, sourceChecks} = require('../static/consult-results.js');

function assignment(id, capability, description, cost, source='catalog', tool='Smartsheet') {
  return {component_id:id, capability, description, monthly_cost:cost, source,
    tool_id:tool.toLowerCase(), tool_name:tool, plan_name:'Pro (annual billing)',
    site_url:'https://www.smartsheet.com/', pricing_source_url:'https://www.smartsheet.com/pricing',
    vetted:true, tool_description:'Shared budget planning and tracking'};
}
const budget = {assignments:[
  assignment('c1','budget_planning','Develop a budget for each fiscal year',27),
  assignment('c2','budget_tracking','Track actual spending against budget',0)
]};
const groups = groupTools(budget);
assert.equal(groups.length,1,'the same tool should appear only once');
assert.equal(groups[0].name,'Smartsheet');
assert.equal(groups[0].cost,27,'second included task must not duplicate price');
assert.equal(groups[0].tasks.length,2);
assert.equal(groups[0].site,'https://www.smartsheet.com/');
assert.equal(groups[0].pricingUrl,'https://www.smartsheet.com/pricing');
assert.equal(uniqueTasks(budget).length,2);

const native = {assignments:[
  {...assignment('c1','form_capture','Capture form entries',0),source:'native',tool_name:'Built into HubSpot',tool_id:'hubspot'},
  {...assignment('c2','crm_contacts','Save contacts',0),tool_id:'hubspot',tool_name:'HubSpot',plan_name:'Free'}
]};
assert.equal(groupTools(native).length,1);
assert.equal(groupTools(native)[0].name,'HubSpot','native assignment must not replace product identity');

const bundle = {assignments:[
  assignment('c1','budget_planning','Plan',27),
  assignment('c2','crm_contacts','CRM',15,'catalog','Airtable')
]};
assert.equal(groupTools(bundle).length,2,'distinct products remain distinct in a bundle');
assert.equal(billingLabel('Pro (annual billing)'),'Annual commitment');
assert.equal(billingLabel('Team (monthly billing)'),'Billed monthly');
const curated = sourceChecks({vetted:true,site:'https://www.smartsheet.com/',pricing_source_url:'https://www.smartsheet.com/pricing',
  monthly_cost:27,pricing_checked_at:'2026-11-08'});
assert.equal(curated.status,'Catalog listing reviewed');
assert.equal(curated.hasVendorLink,true);
assert.equal(curated.hasPricingLink,true);
assert.equal(curated.priceShown,true);
assert.equal(curated.checkedAt,'2026-11-08');
const unreviewed = sourceChecks({vetted:false,site:'http://unsafe.example',pricing_source_url:'',
  monthly_cost:null,pricing_checked_at:''});
assert.equal(unreviewed.status,'Web-discovered; not independently reviewed');
assert.equal(unreviewed.hasVendorLink,false);
assert.equal(unreviewed.hasPricingLink,false);
assert.equal(unreviewed.priceShown,false);
console.log('Recommendation grouping tests passed');
