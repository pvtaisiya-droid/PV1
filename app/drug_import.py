"""ICH G.k incoming structures, not an outbound or dictionary validator."""
import json
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree as ET
from app.e2b_r3_identity import source_date
from app.reaction_semantics import H, OBS, MEDDRA, path

PROFILE = "ich-r3-drug-v1"
BASE = "controlActProcess/subject/investigationEvent/component/adverseEventAssessment"
ORG = "2.16.840.1.113883.3.989.2.1.1.20"
ROLE = "2.16.840.1.113883.3.989.2.1.1.13"
RECHALLENGE = "2.16.840.1.113883.3.989.2.1.1.16"
ROLES = {"1":"suspect","2":"concomitant","3":"interacting","4":"not_administered"}
RECHALLENGES = {"1":"повторное назначение: да; реакция повторилась",
    "2":"повторное назначение: да; реакция не повторилась",
    "3":"повторное назначение: да; результат неизвестен",
    "4":"повторного назначения не было; результат неприменим"}


def key(node):
    if node is None or node.get("nullFlavor") or not node.get("root"):
        return None
    return json.dumps([node.get("root"),node.get("extension")],ensure_ascii=False)


def attrs(node):
    return dict(node.attrib) if node is not None else {}


def text(node):
    return "".join(node.itertext()) if node is not None else None


def one(nodes):
    return nodes[0] if len(nodes)==1 else None


def coded(node):
    return dict(code=node.get("code"),code_system=node.get("codeSystem"),version=node.get("codeSystemVersion"),
        null_flavor=node.get("nullFlavor"),display_name=node.get("displayName"))


def state(node, attribute="value"):
    if node is None:return "missing"
    if node.get("nullFlavor") is not None:return "invalid" if node.get(attribute) is not None else "null_flavor"
    if node.get(attribute) is None:return "invalid"
    return "received_unvalidated"


def known_code(node, code, system=OBS, version="1.1"):
    return node is not None and node.get("code")==code and node.get("codeSystem")==system and node.get("codeSystemVersion") in (None,version)


def source(node, index, **extra):
    return dict(profile=PROFILE,repeat_index=index,xml=ET.tostring(node,encoding="unicode"),
                namespace=H[1:-1],dictionary_validation="not_performed",**extra)


def extract_products(root):
    messages=[root] if root.tag==H+"PORR_IN049016UV" else root.findall(H+"PORR_IN049016UV")
    products=[]
    for message in messages:
        for assessment in message.findall(path(BASE)):
            administrations=[]
            for org in assessment.findall(path("subject1/primaryRole/subjectOf2/organizer")):
                # The structural context retains unknown/missing organiser codes,
                # but flags them instead of guessing an alternative drug meaning.
                administrations.extend((org,a) for a in org.findall(path("component/substanceAdministration")))
            rx_ids={}
            for obs in assessment.findall(path("subject1/primaryRole/subjectOf2/observation")):
                if known_code(obs.find(H+"code"),"29"):
                    for i in obs.findall(H+"id"):
                        if key(i):rx_ids.setdefault(key(i),[]).append(obs)
            product_ids={}
            for _,a in administrations:
                for i in a.findall(H+"id"):
                    if key(i):product_ids.setdefault(key(i),[]).append(a)
            global_assessments=assessment.findall(path("component/causalityAssessment"))
            for index,(org,a) in enumerate(administrations,1):
                warnings=[]
                def warn(message,blocking=False):
                    warnings.append(dict(type="drug_block",message=message,blocking=blocking))
                if not known_code(org.find(H+"code"),"4",ORG,"1.0"):
                    warn("Контекст drugInformation имеет неподтверждённый код; исходные сведения сохранены для проверки.")
                own_keys={key(i) for i in a.findall(H+"id")} - {None}
                if any(len(product_ids[k])!=1 for k in own_keys):
                    warn("Неоднозначные XML ID препаратов; связи не могут быть установлены безопасно.",True)
                def resolve(nodes, registry):
                    keys=[key(n) for n in nodes]
                    if len(keys)!=1 or keys[0] is None or len(registry.get(keys[0],[]))!=1:
                        warn("Не разрешена однозначная XML-ссылка препарата/реакции. Импорт блока остановлен.",True)
                        return None
                    return keys[0]
                kind=one(a.findall(path("consumable/instanceOfKind/kindOfProduct")))
                if kind is None:
                    warn("Нет единственного контекста идентификации препарата; требуется разбор исходника.",True)
                    kind=ET.Element(H+"kindOfProduct")
                p=dict(reported_product_name=text(kind.find(H+"name")),drug_role="unknown",
                    drug_profile=PROFILE,normalized_product_name=None,active_substances=[],dosages=[],
                    indications=[],substances=[],assessments=[],rechallenges=[],warnings=warnings,
                    coded_identifiers=[coded(c) for c in kind.findall(H+"code")],source_facts=source(a,index,
                        xml_ids=[attrs(i) for i in a.findall(H+"id")],organizer_code=attrs(org.find(H+"code")),
                        warnings=warnings,xml_path=BASE+"/subject1/primaryRole/subjectOf2/organizer/component/substanceAdministration"))
                approval=kind.find(path("asManufacturedProduct/subjectOf/approval"))
                if approval is not None:
                    aid=approval.find(H+"id")
                    p.update(authorization_number=aid.get("extension") if aid is not None else None,
                        authorization_holder=text(approval.find(path("holder/role/playingOrganization/name"))),
                        authorization_country_code=attrs(approval.find(path("author/territorialAuthority/territory/code"))).get("code"))
                role_nodes=[]
                for ca in global_assessments:
                    code=ca.find(H+"code")
                    if code is None or code.get("code") not in ("20","39"):continue
                    reference=resolve(ca.findall(path("subject2/productUseReference/id")),product_ids)
                    if reference not in own_keys:continue
                    if not known_code(code,code.get("code")):
                        warn("Неподдержанный код системы роли/причинности; источник сохранён, сопоставление остановлено.",True)
                        continue
                    if code.get("code")=="20":
                        role_nodes.extend(ca.findall(H+"value"))
                    else:
                        reaction_ref=resolve(ca.findall(path("subject1/adverseEffectReference/id")),rx_ids)
                        if reaction_ref is not None:
                            p["assessments"].append(dict(reaction_ref=reaction_ref,
                                assessment_source=text(ca.find(path("author/assignedEntity/code/originalText"))),
                                assessment_method_text=text(ca.find(path("methodCode/originalText"))),relatedness=text(ca.find(H+"value")),
                                source_facts=source(ca,len(p["assessments"])+1)))
                role=one(role_nodes)
                p["source_facts"]["role"] = dict(state="missing" if not role_nodes else "ambiguous" if role is None else state(role,"code"),candidates=[attrs(n) for n in role_nodes])
                if role is not None and role.get("codeSystem")==ROLE and role.get("codeSystemVersion") in (None,"1.0") and not role.get("nullFlavor"):
                    p["drug_role"]=ROLES.get(role.get("code"),"unknown")
                if p["drug_role"]=="unknown":warn("Роль препарата не установлена; исходный код/неизвестность сохранены, suspect не подставлен.")
                if role is not None and state(role,"code")=="received_unvalidated":
                    p["source_facts"]["role"]["state"]="interpreted" if p["drug_role"]!="unknown" else "unsupported"
                for n,ingredient in enumerate(kind.findall(H+"ingredient"),1):
                    if ingredient.get("classCode") not in (None,"ACTI"):
                        warn("Ингредиент не имеет контекста действующего вещества ACTI; импорт остановлен для разбора источника.",True)
                    sub=ingredient.find(H+"ingredientSubstance"); ident=sub.find(H+"code") if sub is not None else None
                    num=ingredient.find(path("quantity/numerator")); den=ingredient.find(path("quantity/denominator"))
                    s=dict(reported_substance_name=text(sub.find(H+"name")) if sub is not None else None,
                        identifier_code=attrs(ident).get("code"),identifier_system=attrs(ident).get("codeSystem"),identifier_version=attrs(ident).get("codeSystemVersion"),
                        strength_value=attrs(num).get("value"),strength_unit=attrs(num).get("unit"),
                        denominator_value=attrs(den).get("value"),denominator_unit=attrs(den).get("unit"),
                        quantity_null_flavor=attrs(ingredient.find(H+"quantity")).get("nullFlavor"),
                        source_facts=source(ingredient,n,quantity=dict(numerator=attrs(num),denominator=attrs(den))))
                    p["substances"].append(s)
                    if s["reported_substance_name"]:p["active_substances"].append(s["reported_substance_name"])
                for n,dose in enumerate(a.findall(path("outboundRelationship2/substanceAdministration")),1):
                    for field in ('effectiveTime','doseQuantity','routeCode'):
                        if len(dose.findall(H+field))>1:
                            warn("Неоднозначная структура режима: несколько одиночных элементов. Исходник сохранён, импорт остановлен.",True)
                    dq=dose.find(H+"doseQuantity"); num=dose.find(path("doseQuantity/numerator")); den=dose.find(path("doseQuantity/denominator"))
                    eff=dose.find(H+"effectiveTime")
                    lows=eff.findall('.//'+H+'low') if eff is not None else []
                    highs=eff.findall('.//'+H+'high') if eff is not None else []
                    period=one(eff.findall('.//'+H+'period')) if eff is not None else None
                    width=one(eff.findall('.//'+H+'width')) if eff is not None else None
                    if eff is not None and any(len(eff.findall('.//'+H+tag))>1 for tag in ('period','width')):
                        warn("Несколько интервалов/длительностей в одном режиме; безопасное преобразование не установлено.",True)
                    start=source_date(lows,"effectiveTime//low","G.k.4.r.4")
                    end=source_date(highs,"effectiveTime//high","G.k.4.r.5")
                    route=dose.find(H+"routeCode"); form=dose.find(path("consumable/instanceOfKind/kindOfProduct/formCode"))
                    d=dict(dose_value=attrs(dq).get("value"),dose_unit=attrs(dq).get("unit"),
                        dose_numerator_value=attrs(num).get("value"),dose_numerator_unit=attrs(num).get("unit"),
                        dose_denominator_value=attrs(den).get("value"),dose_denominator_unit=attrs(den).get("unit"),
                        interval_value=attrs(period).get("value"),interval_unit=attrs(period).get("unit"),
                        start_raw=start["raw"],start_null_flavor=start["null_flavor"],end_raw=end["raw"],end_null_flavor=end["null_flavor"],
                        duration_value=attrs(width).get("value"),duration_unit=attrs(width).get("unit"),dosage_text=text(dose.find(H+"text")),
                        route_text=text(route.find(H+"originalText")) if route is not None else None,
                        route_code=attrs(route).get("code"),route_system=attrs(route).get("codeSystem"),route_version=attrs(route).get("codeSystemVersion"),route_null_flavor=attrs(route).get("nullFlavor"),
                        dosage_form_text=text(form.find(H+"originalText")) if form is not None else None,
                        dosage_form_code=attrs(form).get("code"),dosage_form_system=attrs(form).get("codeSystem"),dosage_form_version=attrs(form).get("codeSystemVersion"),
                        batch_lot_number=text(dose.find(path("consumable/instanceOfKind/productInstanceInstance/lotNumberText"))),
                        source_facts=source(dose,n,start=start,end=end,dose=attrs(dq),numerator=attrs(num),denominator=attrs(den),period=attrs(period),width=attrs(width)))
                    for pr in dose.findall(path("inboundRelationship/observation")):
                        if known_code(pr.find(H+"code"),"28"):
                            v=pr.find(H+"value")
                            d.update(parent_route_text=text(v.find(H+"originalText")) if v is not None else None,
                                parent_route_code=attrs(v).get("code"),parent_route_system=attrs(v).get("codeSystem"),parent_route_version=attrs(v).get("codeSystemVersion"))
                    if start["state"] in ("invalid","ambiguous") or end["state"] in ("invalid","ambiguous"):
                        warn("Дата режима не разобрана однозначно; исходная строка/XML сохранены, дата не дополнена.")
                    p["dosages"].append(d)
                for n,ind in enumerate(a.findall(path("inboundRelationship/observation")),1):
                    c=ind.find(H+"code")
                    if c is None or c.get("code")!="19":continue
                    v=one(ind.findall(H+"value")); supported=known_code(c,"19") and attrs(v).get("codeSystem")==MEDDRA
                    p["indications"].append(dict(reported_text=text(v.find(H+"originalText")) if v is not None else None,
                        meddra_llt_code=attrs(v).get("code") if supported and not attrs(v).get("nullFlavor") else None,
                        meddra_version=attrs(v).get("codeSystemVersion"),null_flavor=attrs(v).get("nullFlavor"),
                        source_facts=source(ind,n,value=attrs(v),state=state(v,"code") if supported else "unsupported",code_level="LLT" if supported else "unestablished")))
                    if not supported:warn("Код показания не интерпретирован как MedDRA LLT; исходные атрибуты сохранены.")
                for n,rech in enumerate(a.findall(path("outboundRelationship2/observation")),1):
                    c=rech.find(H+"code")
                    if c is None or c.get("code")!="31":continue
                    ref=resolve(rech.findall(path("outboundRelationship1/actReference/id")),rx_ids)
                    v=one(rech.findall(H+"value"))
                    if not known_code(c,"31"):
                        warn("Не подтверждён контекст rechallenge; импорт связи остановлен.",True)
                    if ref is not None:
                        p["rechallenges"].append(dict(reaction_ref=ref,rechallenge_code=attrs(v).get("code"),code_system=attrs(v).get("codeSystem"),
                            code_version=attrs(v).get("codeSystemVersion"),null_flavor=attrs(v).get("nullFlavor"),source_facts=source(rech,n,value=attrs(v))))
                        if attrs(v).get("codeSystem")!=RECHALLENGE or attrs(v).get("codeSystemVersion") not in (None,"1.0") or attrs(v).get("code") not in RECHALLENGES or attrs(v).get("nullFlavor"):
                            warn("Неподдержанное/неизвестное значение rechallenge сохранено без медицинского заключения.")
                for group in ('dosages','substances'):
                    for row in p[group]:
                        for field in ('dose_value','dose_numerator_value','dose_denominator_value','interval_value','duration_value','strength_value','denominator_value'):
                            value=row.get(field)
                            if value is None:continue
                            try:valid=Decimal(value).is_finite()
                            except InvalidOperation:valid=False
                            if not valid:warn("Числовое значение препарата не разобрано; исходная строка сохранена без вычислений.")
                products.append(p)
    return products
