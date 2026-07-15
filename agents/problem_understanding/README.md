# 模块 1：问题理解 Agent 输入输出字段与格式设计

---

## 1. 输入模块

问题理解 Agent 从共享上下文中读取用户输入：

```text
task_context.user_input
```

### 1.1 输入格式

```json
{
  "original_question": "阿尔茨海默病的关键致病机制是什么？",
  "user_constraints": {
    "language": "zh",
    "domain_preference": "biomedicine",
    "output_detail_level": "standard"
  }
}
```

### 1.2 输入字段说明

| 字段                                     | 类型     | 是否必需 | 说明                                                                 |
| -------------------------------------- | ------ | ---- | ------------------------------------------------------------------ |
| `original_question`                    | string | 必需   | 用户输入的原始科学问题，是问题理解 Agent 需要解析的核心内容。                                 |
| `user_constraints`                     | object | 可选   | 用户对输出结果的附加约束。                                                      |
| `user_constraints.language`            | string | 可选   | 输出语言，例如中文 `zh`、英文 `en`。                                            |
| `user_constraints.domain_preference`   | string | 可选   | 用户指定的领域偏好，例如 `biomedicine`、`materials_science`、`computer_science`。 |
| `user_constraints.output_detail_level` | string | 可选   | 输出详细程度，可取 `brief`、`standard`、`detailed`。                           |

### 1.3 最小输入格式

```json
{
  "original_question": "阿尔茨海默病的关键致病机制是什么？"
}
```

---

## 2. 输出模块

问题理解 Agent 将结构化结果写入共享上下文：

```text
task_context.question_card
```

### 2.1 输出格式

```json
{
  "question_card": {
    "question_id": "q_001",
    "original_question": "阿尔茨海默病的关键致病机制是什么？",
    "core_question": "哪些分子、细胞和系统层面的机制驱动阿尔茨海默病的发生与进展？",
    "question_type": "mechanism_analysis",
    "domain": ["神经科学", "医学", "分子生物学"],
    "research_object": {
      "name": "阿尔茨海默病",
      "type": "disease",
      "aliases": ["Alzheimer's disease", "AD"]
    },
    "key_concepts": [
      {
        "name": "Aβ沉积",
        "normalized_name": "amyloid beta deposition",
        "category": "pathological_process"
      },
      {
        "name": "Tau蛋白异常",
        "normalized_name": "tau pathology",
        "category": "molecular_mechanism"
      },
      {
        "name": "神经炎症",
        "normalized_name": "neuroinflammation",
        "category": "cellular_process"
      }
    ],
    "key_variables": [
      {
        "name": "Aβ沉积",
        "type": "causal_factor",
        "role": "potential_driver"
      },
      {
        "name": "Tau蛋白异常",
        "type": "mediator",
        "role": "disease_progression_factor"
      },
      {
        "name": "认知下降",
        "type": "outcome",
        "role": "clinical_endpoint"
      }
    ],
    "sub_questions": [
      {
        "sub_question_id": "sq_001",
        "content": "Aβ沉积是否是阿尔茨海默病发生的早期启动因素？"
      },
      {
        "sub_question_id": "sq_002",
        "content": "Tau蛋白异常如何影响神经元功能和认知下降？"
      },
      {
        "sub_question_id": "sq_003",
        "content": "神经炎症是否会放大阿尔茨海默病的病理进展？"
      }
    ],
    "research_scope": {
      "included": ["机制解释", "候选假设生成", "可验证研究问题拆解"],
      "excluded": ["个体诊断", "临床用药建议", "治疗方案推荐"]
    },
    "search_keywords": {
      "zh": ["阿尔茨海默病", "致病机制", "Aβ沉积", "Tau蛋白", "神经炎症"],
      "en": ["Alzheimer disease mechanism", "amyloid beta", "tau pathology", "neuroinflammation", "synaptic dysfunction"]
    }
  }
}
```

---

## 3. 输出字段说明

| 字段                              | 类型            | 是否必需 | 说明                                            |
| ------------------------------- | ------------- | ---- | --------------------------------------------- |
| `question_card`                 | object        | 必需   | 问题理解 Agent 生成的结构化问题卡片。                        |
| `question_id`                   | string        | 必需   | 问题编号，用于任务追踪。                                  |
| `original_question`             | string        | 必需   | 用户输入的原始问题，保持不改写。                              |
| `core_question`                 | string        | 必需   | 标准化后的核心研究问题。                                  |
| `question_type`                 | string        | 必需   | 问题类型，例如机制分析、因果关系、比较分析等。                       |
| `domain`                        | array[string] | 必需   | 问题所属学科领域。                                     |
| `research_object`               | object        | 必需   | 研究对象信息。                                       |
| `research_object.name`          | string        | 必需   | 研究对象名称。                                       |
| `research_object.type`          | string        | 必需   | 研究对象类型，例如疾病、基因、蛋白、材料、方法等。                     |
| `research_object.aliases`       | array[string] | 可选   | 研究对象的英文名、缩写或别名。                               |
| `key_concepts`                  | array[object] | 必需   | 关键科学概念列表。                                     |
| `key_concepts.name`             | string        | 必需   | 概念名称。                                         |
| `key_concepts.normalized_name`  | string        | 可选   | 规范化英文名称，便于检索。                                 |
| `key_concepts.category`         | string        | 可选   | 概念类别。                                         |
| `key_variables`                 | array[object] | 必需   | 关键变量列表。                                       |
| `key_variables.name`            | string        | 必需   | 变量名称。                                         |
| `key_variables.type`            | string        | 必需   | 变量类型，例如 `causal_factor`、`mediator`、`outcome`。 |
| `key_variables.role`            | string        | 可选   | 变量在研究问题中的作用。                                  |
| `sub_questions`                 | array[object] | 必需   | 由核心问题拆解出的子问题。                                 |
| `sub_questions.sub_question_id` | string        | 必需   | 子问题编号。                                        |
| `sub_questions.content`         | string        | 必需   | 子问题内容。                                        |
| `research_scope`                | object        | 必需   | 研究范围。                                         |
| `research_scope.included`       | array[string] | 必需   | 本问题包含的研究内容。                                   |
| `research_scope.excluded`       | array[string] | 必需   | 本问题排除的内容。                                     |
| `search_keywords`               | object        | 必需   | 检索关键词。                                        |
| `search_keywords.zh`            | array[string] | 可选   | 中文检索关键词。                                      |
| `search_keywords.en`            | array[string] | 必需   | 英文检索关键词。                                      |

---

## 4. 标准字段模板

### 4.1 输入模板

```json
{
  "original_question": "string",
  "user_constraints": {
    "language": "string",
    "domain_preference": "string",
    "output_detail_level": "string"
  }
}
```

### 4.2 输出模板

```json
{
  "question_card": {
    "question_id": "string",
    "original_question": "string",
    "core_question": "string",
    "question_type": "string",
    "domain": ["string"],
    "research_object": {
      "name": "string",
      "type": "string",
      "aliases": ["string"]
    },
    "key_concepts": [
      {
        "name": "string",
        "normalized_name": "string",
        "category": "string"
      }
    ],
    "key_variables": [
      {
        "name": "string",
        "type": "string",
        "role": "string"
      }
    ],
    "sub_questions": [
      {
        "sub_question_id": "string",
        "content": "string"
      }
    ],
    "research_scope": {
      "included": ["string"],
      "excluded": ["string"]
    },
    "search_keywords": {
      "zh": ["string"],
      "en": ["string"]
    }
  }
}
```

---

## 5. 运行与使用

本模块是 AI for Science 多 Agent 流程中的第一步，负责将前沿科学问题转化为结构化的 `question_card`。批量运行时，输入文件为 `questions_125.json`，输出文件为 `question_cards_125.json`。

### 5.1 环境安装

进入项目目录后，先安装依赖：

```bash
pip install -r problem_understanding/requirements.txt
```

### 5.2 配置 API

项目提供 `config_example.json` 作为配置模板。首次使用时，复制一份并命名为 `config.json`：

```bash
cp config_example.json config.json
```

在 Windows PowerShell 中可以使用：

```powershell
copy config_example.json config.json
```

然后在 `config.json` 中填写自己的 API 信息。可以配置多个 `providers`，当某个 API 调用失败时，程序会自动尝试下一个接口，避免单个接口异常导致整个批处理失败。

### 5.3 输入文件

输入文件为：

```text
questions_125.json
```

每条数据包含问题编号、学科信息、原始问题和问题描述：

```json
{
  "id": "q_001",
  "discipline_en": "Mathematical Sciences",
  "discipline_zh": "数学科学",
  "question_en": "What makes prime numbers so special?",
  "question_description_en": "There are infinite prime numbers..."
}
```

其中 `question_en` 和 `question_description_en` 会共同作为问题理解 Agent 的输入。

### 5.4 批量运行

在项目根目录运行：

```bash
python run_batch.py
```

运行后，程序会处理 `questions_125.json` 中的问题，并生成：

```text
question_cards_125.json
```

输出文件中包含每个问题对应的结构化问题卡片。

### 5.5 单条调用方式

除批量运行外，也可以在 Python 中直接调用 Agent：

```python
from problem_understanding.agent import ProblemUnderstandingAgent
from problem_understanding.llm_client import LLMClient

agent = ProblemUnderstandingAgent(llm=LLMClient())

result = agent.run(
    user_input={
        "original_question": "What makes prime numbers so special?",
        "question_description": "There are infinite prime numbers that are only divisible by one and themselves.",
        "user_constraints": {
            "language": "en",
            "domain_preference": "Mathematical Sciences"
        }
    },
    question_id="q_001"
)

question_card = result["data"]["question_card"]
```

### 5.6 注意事项

- 如果没有有效 API，程序会自动进入 mock 模式，用于格式测试，但输出内容只适合联调，不适合作为最终结果。
- 若某个 API 失败，程序会自动尝试下一个 provider。
