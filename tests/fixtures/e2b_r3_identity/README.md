# Источники и границы примера

`source.xml` — небольшой самостоятельно составленный синтетический пример для проверки ID и дат. Не сообщение РЗН, не полная ICSR и не эталон XSD-валидности. Клинические элементы нужны только существующему сценарию импорта PV1. Тесты переставляют ID, меняют отправителя и даты, добавляют неизвестные/дублирующиеся ID; работа не зависит от `.tmp-stage-1`.

Основание: [официальный ICH E2B(R3) package 1.11](https://admin.ich.org/sites/default/files/inline-files/IG_Complete_Package_v1_11_1.zip), январь 2026, SHA-256 `a3e3f4e2fbec61abec6a3166cb96cc8236f496b08554f71ff661e4ae8172ff72`. [Каталог ICH](https://admin.ich.org/node/348).

- [Implementation Guide v5.03, 18.07.2025](https://database.ich.org/sites/default/files/ICH_E2B%28R3%29_EWG_IWG_ICSR_Implementation_Guide_%28QA%20integration%29_Step3_2025_0718_Assembly_Approved_0.pdf), §§N.1.2, N.2.r.1–3, C.1.1, C.1.4, C.1.5, C.1.8.1 и Appendix II. C.1.4 — первое получение из источника; C.1.5 — получение наиболее свежей информации. Это не регистрация в PV1.
- [Appendix I(G) v1.03, 18.07.2025](https://database.ich.org/sites/default/files/ICH_E2B%28R3%29_EWG_IWG_Appendix_I%28G%29_Step3_2025_0718_Assembly_Approved_0.pdf), физические страницы PDF 7–9, 11: технические пути и OID. Подтверждены по таблицам, не только по одному XML-примеру.
- В пакете: `5_Reference Instances/00_ICH_ICSR_Reference_Instance_variation_v3_1.xml` дополнительно подтверждает контексты; `4_ICH_ICSR_Schema_Files` задаёт HL7 namespace `urn:hl7-org:v3`.

Во всех путях ниже namespace HL7. Полный префикс OID: `2.16.840.1.113883.3.989.2.1.3.`.

| Элемент | Контекст | Завершение root | Значение |
|---|---|---|---|
| N.1.2 | MCCI_IN200100UV01/id | 22 | extension |
| N.2.r.1 | PORR_IN049016UV/id | 1 | extension |
| N.2.r.2 / N.2.r.3 | PORR_IN049016UV/sender либо receiver/device/id | 11 / 12 | extension |
| C.1.1 | PORR_IN049016UV/controlActProcess/subject/investigationEvent/id | 1 | extension |
| C.1.8.1 | тот же контекст investigationEvent/id | 2 | extension |
| C.1.4 | investigationEvent/effectiveTime/low | — | value / nullFlavor |
| C.1.5 | investigationEvent/availabilityTime | — | value / nullFlavor |

N.2.r.1 и C.1.1 имеют одинаковый root, но разное назначение по контексту. Для локального ID PV1 сохраняет точную пару root/extension отправителя N.2.r.2 как пространство; `assigningAuthorityName` сохраняется, но не используется как ключ вместо идентификатора. Это консервативное решение сопоставления PV1, не региональное правило РЗН. Суффикс любого ID не объявляется содержательной версией.

Поддержан один PORR в XML, в оболочке MCCI или непосредственно корнем. Несколько PORR, неоднозначные/неизвестные ID не выбираются по порядку: исходный файл остаётся для разбора, подтверждение импорта блокируется. Синтаксическое сохранение TS/nullFlavor не доказывает допустимость для представления: в том числе частичная дата сохраняется без придумывания дня, даже если конкретное поле IG требует большей точности. Необычный/ошибочный TS сохраняется с состоянием invalid, а не исправляется.
