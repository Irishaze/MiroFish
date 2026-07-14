// 实体/关系类型名称必须是英文（Zep本体API的硬性要求：PascalCase实体类型、UPPER_SNAKE_CASE关系类型），
// 此处仅用于中文环境下的展示层翻译，不影响底层与 Zep 交互使用的实际类型名。
// 覆盖 ontology_generator.py 中约定的核心推理类型、兜底类型与关系类型词表；
// 项目特定生成的类型（如某次生成的 "ClimateModel"）若不在此表中，则展示层回退显示原始英文类型名。
export const TYPE_LABELS_ZH = {
  Hypothesis: '假设', Evidence: '证据', Claim: '论断', Source: '来源',
  Method: '方法', Variable: '变量', Mechanism: '机制', Finding: '发现',
  Concept: '概念', Researcher: '研究者', Entity: '实体',
  SUPPORTS: '支持', REFUTES: '反驳', CONTRADICTS: '相互矛盾', CITES: '引用',
  TESTS: '检验', MEASURES: '测量', PRODUCES: '产生', EXPLAINS: '解释',
  DERIVED_FROM: '修订自', AUTHORED_BY: '提出者', RELATES_TO: '相关',
  RELATED_TO: '相关', RELATED: '相关', SELF_LOOP: '自环关系',
}

// 类型展示标签：中文环境下查表翻译，未命中或英文环境则回退显示原始类型名
export function typeLabel(rawType, locale) {
  if (!rawType) return rawType
  if (locale !== 'zh') return rawType
  return TYPE_LABELS_ZH[rawType] || rawType
}
