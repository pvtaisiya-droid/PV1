"""Rebuild reviewed structural recipes from the pinned official ICH package.

Development tool only. No source clinical values are copied into the recipes.
Usage: python tools/build_r3_layout.py <extracted-package-directory>
"""
import hashlib
import json
from pathlib import Path
import re
import sys
from lxml import etree as E

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = Path(sys.argv[1]).resolve()
OUT = ROOT / 'app/resources/ich-r3-1.11'
OUT.mkdir(parents=True, exist_ok=True)
NS = {'h': 'urn:hl7-org:v3', 'xsi': 'http://www.w3.org/2001/XMLSchema-instance'}
H, X = '{'+NS['h']+'}', '{'+NS['xsi']+'}'
ref_path = PACKAGE / '5_Reference Instances/00_ICH_ICSR_Reference_Instance_variation_v3_1.xml'
if hashlib.sha256(ref_path.read_bytes()).hexdigest() != 'ef55d8c7c7948d2cb77b35ab72aa1e1e09ca8a384023f7a82bedc25d24662952':
    raise ValueError('Expected the unchanged ICH package 1.11 reference variation 3.1.')
tree = E.parse(str(ref_path), E.XMLParser(remove_comments=True, resolve_entities=False, no_network=True))
root = tree.getroot()
contract = json.loads((ROOT/'app/clinical_contract.json').read_text('utf-8'))
nodes = {}
def q(node, xp):
    found = node.xpath(xp, namespaces=NS)
    if not found:
        raise ValueError('Reference context missing: '+xp)
    return found[0]

def spec(n):
    key = tree.getpath(n)
    if key in nodes:
        return key
    attrs = {k:v for k,v in n.attrib.items() if k in
             ('classCode','moodCode','typeCode','determinerCode','contextConductionInd', X+'type','inversionInd','operator')}
    for k in ('code','codeSystem','codeSystemVersion','root'):
        v = n.get(k)
        if v and not re.match(r'^[CDEFGHN]\.', v) and not v.startswith('TBD-') and (k != 'root' or re.fullmatch(r'[0-9.]+',v)):
            attrs[k] = v
    # Reference sample numbers, dates, names, language, units, NF and UUIDs are NEVER defaults.
    parent = n.getparent()
    nodes[key] = dict(tag=E.QName(n).localname, attrs=attrs, order=parent.index(n) if parent is not None else 0, constants=[])
    for child in n:
        if E.QName(child).localname in ('code','interpretationCode','statusCode') and child.get('code') and not re.match(r'^[CDEFGHN]\.',child.get('code')):
            nodes[key]['constants'].append(spec(child))
    return key

def trail(parent, child):
    path=[]
    while child is not parent:
        if child is None:
            raise ValueError('Not a descendant')
        path.insert(0,spec(child));child=child.getparent()
    return path

inv=q(root,contract['report']['xpath']);assessment=q(inv,'h:component/h:adverseEventAssessment')
patient=q(root,contract['patient']['xpath']);drug=q(root,contract['drug_extra']['xpath'])
reaction=q(root,contract['reaction_extra']['xpath']);parent=q(root,contract['parent']['xpath']);study=q(root,contract['study']['xpath'])
anchors={'root':root,'investigation':inv,'assessment':assessment,'patient':patient,'parent':parent,'study':study,'product':drug,'reaction':reaction}
recipes={}
repeat_at={'reporter':0,'literature':0,'source_document':0,'summary':0,'sender_comment':0,'reporter_comment':0,'sender_diagnosis':0,
    'parent':1,'medical_history':2,'drug_history':2,'test':2,'parent_medical_history':2,'parent_drug_history':2,
    'death_cause':0,'autopsy_cause':2,'study_registration':0,'drug_additional':0,'pair_interval':0,'pair_last_interval':0,
    'case_identifier':2,'linked_case':0}
for kind, d in contract.items():
    if not d['xpath']:
        continue
    node=q(root,d['xpath'])
    anchor='investigation'
    if kind in ('patient','sender_comment','reporter_comment','sender_diagnosis'):anchor='assessment'
    if kind in ('parent','medical_history','drug_history','test','death_cause','autopsy_cause','study'):anchor='patient'
    if kind in ('parent_medical_history','parent_drug_history'):anchor='parent'
    if kind=='study_registration':anchor='study'
    if kind.startswith('pair_') or kind in ('drug_extra','drug_additional'):anchor='product'
    if kind=='reaction_extra':anchor='reaction'
    fields={}
    for code,f in d['fields'].items():
        if not f.get('xpath') or 'TBD-' in f['xpath']:
            continue
        if code == 'F.r.3.4':
            target=q(node,'h:value')
        elif code in ('E.i.6a','E.i.6b') and not node.xpath(f['xpath'],namespaces=NS):
            target=E.SubElement(q(node,'h:effectiveTime'),H+'width')
        else:
            target=q(node,f['xpath'])
        if code=='C.1.2':continue  # emitted on controlActProcess, outside investigation
        fields[code]=dict(trail=trail(node,target), attribute=f.get('attribute'), type=f['type'], element=code, xpath=f['xpath'])
    recipes[kind]=dict(anchor=anchor, trail=trail(anchors[anchor],node), repeat_at=repeat_at.get(kind), fields=fields)

def recipe(name, anchor, xpath, repeat, fields):
    node=q(anchors[anchor],xpath)
    result={}
    for field,(code,xp,attr,kind) in fields.items():
        result[field]=dict(element=code, trail=trail(node,q(node,xp)), attribute=attr, type=kind, xpath=xp)
    recipes[name]=dict(anchor=anchor,trail=trail(anchors[anchor],node),repeat_at=repeat,fields=result)
    return node

O='2.16.840.1.113883.3.989.2.1.1.19'
obs=lambda code: f"h:outboundRelationship2/h:observation[h:code[@code='{code}']]/h:value"
recipe('Reaction','patient',"h:subjectOf2/h:observation[h:code[@code='29']]",0,{
    'reported_term':('E.i.1.1a','h:value/h:originalText',None,'text'),
    'meddra_llt_code':('E.i.2.1b','h:value','code','code'),
    'meddra_version':('E.i.2.1a','h:value','codeSystemVersion','text'),
    'outcome':('E.i.7',obs('27'),'code','code'),
    **{f:('E.i.3.2'+chr(97+i),obs(c),'value','boolean') for i,(f,c) in enumerate({
        'seriousness_death':'34','seriousness_life_threatening':'21','seriousness_hospitalization':'33',
        'seriousness_disability':'35','seriousness_congenital_anomaly':'12','seriousness_other_medically_important':'26'}.items())}})
recipe('CaseProduct','patient',"h:subjectOf2/h:organizer[h:code[@code='4']]/h:component/h:substanceAdministration",2,{
    'reported_product_name':('G.k.2.2','h:consumable/h:instanceOfKind/h:kindOfProduct/h:name',None,'text'),
    'authorization_number':('G.k.3.1','h:consumable/h:instanceOfKind/h:kindOfProduct/h:asManufacturedProduct/h:subjectOf/h:approval/h:id','extension','text'),
    'authorization_holder':('G.k.3.3','h:consumable/h:instanceOfKind/h:kindOfProduct/h:asManufacturedProduct/h:subjectOf/h:approval/h:holder/h:role/h:playingOrganization/h:name',None,'text'),
    'authorization_country_code':('G.k.3.2','h:consumable/h:instanceOfKind/h:kindOfProduct/h:asManufacturedProduct/h:subjectOf/h:approval/h:author/h:territorialAuthority/h:territory/h:code','code','code')})
dose=q(drug,'h:outboundRelationship2/h:substanceAdministration')
print('Dose context:', E.QName(dose).localname)
recipe('CaseDrugDosage','product','h:outboundRelationship2/h:substanceAdministration',0,{
    'dose_value':('G.k.4.r.1a','h:doseQuantity','value','decimal'),
    'dose_unit':('G.k.4.r.1b','h:doseQuantity','unit','text'),
    'start_raw':('G.k.4.r.4','h:effectiveTime/h:comp[2]/h:low','value','date'),
    'end_raw':('G.k.4.r.5','h:effectiveTime/h:comp[2]/h:high','value','date'),
    'duration_value':('G.k.4.r.6a','h:effectiveTime/h:comp[3]/h:width','value','decimal'),
    'duration_unit':('G.k.4.r.6b','h:effectiveTime/h:comp[3]/h:width','unit','text'),
    'interval_value':('G.k.4.r.2','h:effectiveTime/h:comp[1]/h:period','value','decimal'),
    'interval_unit':('G.k.4.r.3','h:effectiveTime/h:comp[1]/h:period','unit','text'),
    'dosage_text':('G.k.4.r.8','h:text',None,'text'),
    'route_text':('G.k.4.r.10.1','h:routeCode/h:originalText',None,'text'),
    'route_code':('G.k.4.r.10.2b','h:routeCode','code','code'),
    'route_version':('G.k.4.r.10.2a','h:routeCode','codeSystemVersion','text'),
    'dosage_form_text':('G.k.4.r.9.1','h:consumable/h:instanceOfKind/h:kindOfProduct/h:formCode/h:originalText',None,'text'),
    'dosage_form_code':('G.k.4.r.9.2b','h:consumable/h:instanceOfKind/h:kindOfProduct/h:formCode','code','code'),
    'dosage_form_version':('G.k.4.r.9.2a','h:consumable/h:instanceOfKind/h:kindOfProduct/h:formCode','codeSystemVersion','text'),
    'batch_lot_number':('G.k.4.r.7','h:consumable/h:instanceOfKind/h:productInstanceInstance/h:lotNumberText',None,'text'),
    'parent_route_text':('G.k.4.r.11.1','h:inboundRelationship/h:observation/h:value/h:originalText',None,'text'),
    'parent_route_code':('G.k.4.r.11.2b','h:inboundRelationship/h:observation/h:value','code','code'),
    'parent_route_version':('G.k.4.r.11.2a','h:inboundRelationship/h:observation/h:value','codeSystemVersion','text')})
recipe('CaseDrugSubstance','product','h:consumable/h:instanceOfKind/h:kindOfProduct/h:ingredient',3,{
    'reported_substance_name':('G.k.2.3.r.1','h:ingredientSubstance/h:name',None,'text'),
    'strength_value':('G.k.2.3.r.3a','h:quantity/h:numerator','value','decimal'),
    'strength_unit':('G.k.2.3.r.3b','h:quantity/h:numerator','unit','text'),
    'denominator_value':('G.k.2.3.r','h:quantity/h:denominator','value','decimal'),
    'denominator_unit':('G.k.2.3.r','h:quantity/h:denominator','unit','text')})
recipe('CaseDrugIndication','product',"h:inboundRelationship/h:observation[h:code[@code='19']]",0,{
    'reported_text':('G.k.7.r.1','h:value/h:originalText',None,'text'),
    'meddra_llt_code':('G.k.7.r.2b','h:value','code','code'),
    'meddra_version':('G.k.7.r.2a','h:value','codeSystemVersion','text')})
recipe('CaseDrugRechallenge','product',"h:outboundRelationship2/h:observation[h:code[@code='31']]",0,{
    'rechallenge_code':('G.k.9.i.4','h:value','code','code'),
    'code_version':('G.k.9.i.4','h:value','codeSystemVersion','text')})
recipe('CaseProductReactionAssessment','assessment',"h:component/h:causalityAssessment[h:code[@code='39']]",0,{
    'assessment_source':('G.k.9.i.2.r.1','h:author/h:assignedEntity/h:code/h:originalText',None,'text'),
    'assessment_method_text':('G.k.9.i.2.r.2','h:methodCode/h:originalText',None,'text'),
    'relatedness':('G.k.9.i.2.r.3','h:value',None,'text')})
recipe('drug_role','assessment',"h:component/h:causalityAssessment[h:code[@code='20']]",0,{'drug_role':('G.k.1','h:value','code','code')})

base={name:trail(root,node) for name,node in anchors.items() if name in ('investigation','assessment','patient')}
layout=dict(version='ich-layout-1',source_sha256=hashlib.sha256(ref_path.read_bytes()).hexdigest(),nodes=nodes,base=base,recipes=recipes)
(OUT/'layout.json').write_text(json.dumps(layout,ensure_ascii=False,indent=2)+'\n','utf-8')
print('Recipes:',len(recipes),'structural nodes:',len(nodes))
