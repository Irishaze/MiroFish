"""
本体生成服务
接口1：分析文本内容，生成适合科学假设检验研究的实体和关系类型定义
"""

import json
import logging
import re
from typing import Dict, Any, List, Optional
from ..utils.llm_client import LLMClient
from ..utils.locale import get_language_instruction

logger = logging.getLogger(__name__)


def _to_pascal_case(name: str) -> str:
    """将任意格式的名称转换为 PascalCase（如 'works_for' -> 'WorksFor', 'person' -> 'Person'）"""
    # 按非字母数字字符分割
    parts = re.split(r'[^a-zA-Z0-9]+', name)
    # 再按 camelCase 边界分割（如 'camelCase' -> ['camel', 'Case']）
    words = []
    for part in parts:
        words.extend(re.sub(r'([a-z])([A-Z])', r'\1_\2', part).split('_'))
    # 每个词首字母大写，过滤空串
    result = ''.join(word.capitalize() for word in words if word)
    return result if result else 'Unknown'


# 本体生成的系统提示词
ONTOLOGY_SYSTEM_PROMPT = """你是一个专业的知识图谱本体设计专家。你的任务是分析给定的文本内容和研究问题，设计适合**科学假设检验研究**的实体类型和关系类型。

**重要：你必须输出有效的JSON格式数据，不要输出任何其他内容。**

## 核心任务背景

我们正在构建一个**科学假设检验引擎**。在这个系统中：
- 一支小规模的专家研究团队会围绕一个研究问题，反复提出假设、检索文献证据、批判性评估证据强度
- 每个实体是研究推理过程中的一个概念性节点：一个待检验的假设、一条证据、一篇文献来源、一种研究方法、一个被测量的变量、一种因果机制、一项具体发现
- 实体之间通过"支持"、"反驳"、"引用"、"检验"等关系相互连接，构成一张可追溯、可验证的证据图谱
- 我们需要还原严谨的科学推理链条：假设从哪里来、被什么证据支持或反驳、证据来自哪个来源、结论的置信度如何随轮次演化

因此，**实体应当是研究推理链条中的概念性节点**，而不是社交媒体上发声的账号：

**可以是**：
- 待检验或已修订的假设（Hypothesis）
- 支持/反驳某假设的具体证据（Evidence）
- 文献来源中作者提出的论断（Claim）
- 论文、报告、数据集等文献来源（Source）
- 研究/实验/分析方法（Method）
- 被测量或操纵的变量、指标（Variable）
- 解释某现象的因果机制或路径（Mechanism）
- 来源自身报告的具体结果（Finding）
- 提出来源或论断的研究者/机构（Researcher）

**不可以是**：
- 与研究问题无关的社交互动行为（如"点赞"、"转发"）
- 泛泛的情绪或立场标签（如"支持方"、"反对方"）
- 无法追溯到具体证据或来源的空泛主题词

## 输出格式

请输出JSON格式，包含以下结构：

```json
{
    "entity_types": [
        {
            "name": "实体类型名称（英文，PascalCase）",
            "description": "简短描述（使用目标语言，不超过100字符）",
            "attributes": [
                {
                    "name": "属性名（英文，snake_case）",
                    "type": "text",
                    "description": "属性描述"
                }
            ],
            "examples": ["示例实体1", "示例实体2"]
        }
    ],
    "edge_types": [
        {
            "name": "关系类型名称（英文，UPPER_SNAKE_CASE）",
            "description": "简短描述（使用目标语言，不超过100字符）",
            "source_targets": [
                {"source": "源实体类型", "target": "目标实体类型"}
            ],
            "attributes": []
        }
    ],
    "analysis_summary": "对文本内容的简要分析说明"
}
```

## 设计指南（极其重要！）

### 1. 实体类型设计 - 必须严格遵守

**数量要求：必须正好10个实体类型**

**层次结构要求（必须同时包含具体类型和兜底类型）**：

你的10个实体类型必须包含以下层次：

A. **兜底类型（必须包含，放在列表最后2个）**：
   - `Concept`: 任何科学概念或研究对象的兜底类型。当一个概念不属于其他更具体的类型时，归入此类。
   - `Researcher`: 任何研究者/作者/机构的兜底类型。当一个来源的提出者不属于其他更具体的类型时，归入此类。

B. **具体类型（8个，根据文本内容设计）**：
   - 针对研究问题和文本中出现的核心概念，设计更具体的类型
   - 建议始终包含 `Hypothesis`（假设）和 `Evidence`（证据）这两个核心类型，因为它们是假设检验循环的骨架
   - 其余类型根据学科领域调整，例如：文本涉及气候科学可以有 `ClimateModel`, `EmissionScenario`；涉及生物学问题可以有 `Gene`, `Pathway`；涉及社会科学问题可以有 `Survey`, `Population`

**为什么需要兜底类型**：
- 文本中会出现各种难以归类的概念，如某个笼统提及的现象或指标
- 如果没有专门的类型匹配，应该被归入 `Concept`
- 同理，未具名或次要的作者/机构应该归入 `Researcher`

**具体类型的设计原则**：
- 从研究问题和文本中识别出高频出现或关键的概念类型
- 每个具体类型应该有明确的边界，避免重叠
- description 必须清晰说明这个类型和兜底类型的区别

### 2. 关系类型设计

- 数量：6-10个
- 关系应该反映科学推理中证据与假设之间的真实逻辑联系（支持、反驳、引用、检验等）
- 确保关系的 source_targets 涵盖你定义的实体类型

### 3. 属性设计

- 每个实体类型1-3个关键属性
- **注意**：属性名不能使用 `name`、`uuid`、`group_id`、`created_at`、`summary`（这些是系统保留字）
- 推荐使用：`statement`, `status`, `confidence`, `strength`, `authors`, `publication_year`, `description` 等

## 实体类型参考

**核心推理类（具体，强烈建议包含）**：
- Hypothesis: 待检验/已修订的假设
- Evidence: 支持或反驳假设的具体证据

**其他具体类型（根据文本内容选择）**：
- Claim: 来源中作者提出的论断
- Source: 论文、报告、数据集等文献来源
- Method: 研究/实验/分析方法
- Variable: 被测量或操纵的变量
- Mechanism: 解释现象的因果机制
- Finding: 来源自身报告的具体结果

**兜底类型**：
- Concept: 任何科学概念（不属于上述具体类型时使用）
- Researcher: 任何研究者/机构（不属于上述具体类型时使用）

## 关系类型参考

- SUPPORTS: 支持（证据/发现 → 假设）
- REFUTES: 反驳（证据/发现 → 假设）
- CONTRADICTS: 相互矛盾（证据 ↔ 证据）
- CITES: 引用（来源 → 来源）
- TESTS: 检验（方法 → 假设）
- MEASURES: 测量（方法 → 变量）
- PRODUCES: 产生（方法 → 发现/证据）
- EXPLAINS: 解释（机制 → 假设/发现）
- DERIVED_FROM: 由...修订而来（假设 → 假设，用于追溯假设在多轮迭代中的演化）
- AUTHORED_BY: 由...提出（来源 → 研究者）
- RELATES_TO: 泛化关联（兜底关系）
"""


class OntologyGenerator:
    """
    本体生成器
    分析文本内容，生成实体和关系类型定义
    """
    
    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm_client = llm_client or LLMClient()
    
    def generate(
        self,
        document_texts: List[str],
        simulation_requirement: str,
        additional_context: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        生成本体定义

        Args:
            document_texts: 文档文本列表
            simulation_requirement: 研究问题描述
            additional_context: 额外上下文

        Returns:
            本体定义（entity_types, edge_types等）
        """
        # 构建用户消息
        user_message = self._build_user_message(
            document_texts,
            simulation_requirement,
            additional_context
        )
        
        lang_instruction = get_language_instruction()
        system_prompt = f"{ONTOLOGY_SYSTEM_PROMPT}\n\n{lang_instruction}\nIMPORTANT: Entity type names MUST be in English PascalCase (e.g., 'PersonEntity', 'MediaOrganization'). Relationship type names MUST be in English UPPER_SNAKE_CASE (e.g., 'WORKS_FOR'). Attribute names MUST be in English snake_case. Only description fields and analysis_summary should use the specified language above."
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ]
        
        # 调用LLM
        result = self.llm_client.chat_json(
            messages=messages,
            temperature=0.3,
            max_tokens=8192
        )
        
        # 验证和后处理
        result = self._validate_and_process(result)
        
        return result
    
    # 传给 LLM 的文本最大长度（5万字）
    MAX_TEXT_LENGTH_FOR_LLM = 50000
    
    def _build_user_message(
        self,
        document_texts: List[str],
        simulation_requirement: str,
        additional_context: Optional[str]
    ) -> str:
        """构建用户消息"""

        # 合并文本
        combined_text = "\n\n---\n\n".join(document_texts)
        original_length = len(combined_text)

        # 如果文本超过5万字，截断（仅影响传给LLM的内容，不影响图谱构建）
        if len(combined_text) > self.MAX_TEXT_LENGTH_FOR_LLM:
            combined_text = combined_text[:self.MAX_TEXT_LENGTH_FOR_LLM]
            combined_text += f"\n\n...(原文共{original_length}字，已截取前{self.MAX_TEXT_LENGTH_FOR_LLM}字用于本体分析)..."

        message = f"""## 研究问题

{simulation_requirement}

## 文档内容

{combined_text}
"""

        if additional_context:
            message += f"""
## 额外说明

{additional_context}
"""

        message += """
请根据以上内容，设计适合科学假设检验研究的实体类型和关系类型。

**必须遵守的规则**：
1. 必须正好输出10个实体类型
2. 最后2个必须是兜底类型：Concept（概念兜底）和 Researcher（研究者兜底）
3. 前8个是根据研究问题和文本内容设计的具体类型，强烈建议包含 Hypothesis 和 Evidence
4. 所有实体类型必须是科学推理链条中的概念性节点，不能是社交互动行为
5. 属性名不能使用 name、uuid、group_id 等保留字，用 statement、confidence 等替代
"""

        return message
    
    def _validate_and_process(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """验证和后处理结果"""
        
        # 确保必要字段存在
        if "entity_types" not in result:
            result["entity_types"] = []
        if "edge_types" not in result:
            result["edge_types"] = []
        if "analysis_summary" not in result:
            result["analysis_summary"] = ""
        
        # 验证实体类型
        # 记录原始名称到 PascalCase 的映射，用于后续修正 edge 的 source_targets 引用
        entity_name_map = {}
        for entity in result["entity_types"]:
            # 强制将 entity name 转为 PascalCase（Zep API 要求）
            if "name" in entity:
                original_name = entity["name"]
                entity["name"] = _to_pascal_case(original_name)
                if entity["name"] != original_name:
                    logger.warning(f"Entity type name '{original_name}' auto-converted to '{entity['name']}'")
                entity_name_map[original_name] = entity["name"]
            if "attributes" not in entity:
                entity["attributes"] = []
            if "examples" not in entity:
                entity["examples"] = []
            # 确保description不超过100字符
            if len(entity.get("description", "")) > 100:
                entity["description"] = entity["description"][:97] + "..."
        
        # 验证关系类型
        for edge in result["edge_types"]:
            # 强制将 edge name 转为 SCREAMING_SNAKE_CASE（Zep API 要求）
            if "name" in edge:
                original_name = edge["name"]
                edge["name"] = original_name.upper()
                if edge["name"] != original_name:
                    logger.warning(f"Edge type name '{original_name}' auto-converted to '{edge['name']}'")
            # 修正 source_targets 中的实体名称引用，与转换后的 PascalCase 保持一致
            for st in edge.get("source_targets", []):
                if st.get("source") in entity_name_map:
                    st["source"] = entity_name_map[st["source"]]
                if st.get("target") in entity_name_map:
                    st["target"] = entity_name_map[st["target"]]
            if "source_targets" not in edge:
                edge["source_targets"] = []
            if "attributes" not in edge:
                edge["attributes"] = []
            if len(edge.get("description", "")) > 100:
                edge["description"] = edge["description"][:97] + "..."
        
        # Zep API 限制：最多 10 个自定义实体类型，最多 10 个自定义边类型
        MAX_ENTITY_TYPES = 10
        MAX_EDGE_TYPES = 10

        # 去重：按 name 去重，保留首次出现的
        seen_names = set()
        deduped = []
        for entity in result["entity_types"]:
            name = entity.get("name", "")
            if name and name not in seen_names:
                seen_names.add(name)
                deduped.append(entity)
            elif name in seen_names:
                logger.warning(f"Duplicate entity type '{name}' removed during validation")
        result["entity_types"] = deduped

        # 兜底类型定义
        concept_fallback = {
            "name": "Concept",
            "description": "Any scientific concept or research object not fitting other specific types.",
            "attributes": [
                {"name": "statement", "type": "text", "description": "Description of the concept"},
                {"name": "category", "type": "text", "description": "Broad category of the concept"}
            ],
            "examples": ["measured outcome", "study population"]
        }

        researcher_fallback = {
            "name": "Researcher",
            "description": "Any researcher, author, or institution not fitting other specific types.",
            "attributes": [
                {"name": "full_name", "type": "text", "description": "Name of the researcher or institution"},
                {"name": "affiliation", "type": "text", "description": "Institutional affiliation"}
            ],
            "examples": ["study author", "research institution"]
        }

        # 检查是否已有兜底类型
        entity_names = {e["name"] for e in result["entity_types"]}
        has_concept = "Concept" in entity_names
        has_researcher = "Researcher" in entity_names

        # 需要添加的兜底类型
        fallbacks_to_add = []
        if not has_concept:
            fallbacks_to_add.append(concept_fallback)
        if not has_researcher:
            fallbacks_to_add.append(researcher_fallback)
        
        if fallbacks_to_add:
            current_count = len(result["entity_types"])
            needed_slots = len(fallbacks_to_add)
            
            # 如果添加后会超过 10 个，需要移除一些现有类型
            if current_count + needed_slots > MAX_ENTITY_TYPES:
                # 计算需要移除多少个
                to_remove = current_count + needed_slots - MAX_ENTITY_TYPES
                # 从末尾移除（保留前面更重要的具体类型）
                result["entity_types"] = result["entity_types"][:-to_remove]
            
            # 添加兜底类型
            result["entity_types"].extend(fallbacks_to_add)
        
        # 最终确保不超过限制（防御性编程）
        if len(result["entity_types"]) > MAX_ENTITY_TYPES:
            result["entity_types"] = result["entity_types"][:MAX_ENTITY_TYPES]
        
        if len(result["edge_types"]) > MAX_EDGE_TYPES:
            result["edge_types"] = result["edge_types"][:MAX_EDGE_TYPES]
        
        return result
    
    def generate_python_code(self, ontology: Dict[str, Any]) -> str:
        """
        将本体定义转换为Python代码（类似ontology.py）
        
        Args:
            ontology: 本体定义
            
        Returns:
            Python代码字符串
        """
        code_lines = [
            '"""',
            '自定义实体类型定义',
            '由MiroFish自动生成，用于科学假设检验研究',
            '"""',
            '',
            'from pydantic import Field',
            'from zep_cloud.external_clients.ontology import EntityModel, EntityText, EdgeModel',
            '',
            '',
            '# ============== 实体类型定义 ==============',
            '',
        ]
        
        # 生成实体类型
        for entity in ontology.get("entity_types", []):
            name = entity["name"]
            desc = entity.get("description", f"A {name} entity.")
            
            code_lines.append(f'class {name}(EntityModel):')
            code_lines.append(f'    """{desc}"""')
            
            attrs = entity.get("attributes", [])
            if attrs:
                for attr in attrs:
                    attr_name = attr["name"]
                    attr_desc = attr.get("description", attr_name)
                    code_lines.append(f'    {attr_name}: EntityText = Field(')
                    code_lines.append(f'        description="{attr_desc}",')
                    code_lines.append(f'        default=None')
                    code_lines.append(f'    )')
            else:
                code_lines.append('    pass')
            
            code_lines.append('')
            code_lines.append('')
        
        code_lines.append('# ============== 关系类型定义 ==============')
        code_lines.append('')
        
        # 生成关系类型
        for edge in ontology.get("edge_types", []):
            name = edge["name"]
            # 转换为PascalCase类名
            class_name = ''.join(word.capitalize() for word in name.split('_'))
            desc = edge.get("description", f"A {name} relationship.")
            
            code_lines.append(f'class {class_name}(EdgeModel):')
            code_lines.append(f'    """{desc}"""')
            
            attrs = edge.get("attributes", [])
            if attrs:
                for attr in attrs:
                    attr_name = attr["name"]
                    attr_desc = attr.get("description", attr_name)
                    code_lines.append(f'    {attr_name}: EntityText = Field(')
                    code_lines.append(f'        description="{attr_desc}",')
                    code_lines.append(f'        default=None')
                    code_lines.append(f'    )')
            else:
                code_lines.append('    pass')
            
            code_lines.append('')
            code_lines.append('')
        
        # 生成类型字典
        code_lines.append('# ============== 类型配置 ==============')
        code_lines.append('')
        code_lines.append('ENTITY_TYPES = {')
        for entity in ontology.get("entity_types", []):
            name = entity["name"]
            code_lines.append(f'    "{name}": {name},')
        code_lines.append('}')
        code_lines.append('')
        code_lines.append('EDGE_TYPES = {')
        for edge in ontology.get("edge_types", []):
            name = edge["name"]
            class_name = ''.join(word.capitalize() for word in name.split('_'))
            code_lines.append(f'    "{name}": {class_name},')
        code_lines.append('}')
        code_lines.append('')
        
        # 生成边的source_targets映射
        code_lines.append('EDGE_SOURCE_TARGETS = {')
        for edge in ontology.get("edge_types", []):
            name = edge["name"]
            source_targets = edge.get("source_targets", [])
            if source_targets:
                st_list = ', '.join([
                    f'{{"source": "{st.get("source", "Entity")}", "target": "{st.get("target", "Entity")}"}}'
                    for st in source_targets
                ])
                code_lines.append(f'    "{name}": [{st_list}],')
        code_lines.append('}')
        
        return '\n'.join(code_lines)

