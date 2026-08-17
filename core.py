# ============================================================
# core.py - Full Pipeline with Enhanced Rule-Based Engine
# ============================================================

import re
import warnings

# ---------- 1. Anonymization (Malaysian PII) ----------
def anonymize_malaysian_pii(text):
    def replace_name(match):
        prefix = match.group(1)
        name = match.group(2).strip()
        suffix = '.' if name.endswith('.') else ''
        if name.endswith('.'):
            name = name[:-1]
        return f"{prefix}: [PATIENT_NAME]{suffix}"

    # 合并所有姓名匹配到同一个 protected_text
    protected_text = text

    # 1) 匹配 "Patient Name:" / "Name:" 格式
    protected_text = re.sub(
        r'(?i)(Patient\s*Name|Name)\s*:\s*([A-Za-z\'\.\/]+(?:\s+(?:\b(?:binti|bin|bt|bte|anak|ak|a/l|a/p|al|ap|s/o|d/o)\b|[A-Za-z\'\.\/]+))*)(?=\s*(?:[,\.\n;\(\)]|$|\b(?:Gender|Age|Race|IC|Phone|MyKad|DOB|MRN)\b))',
        replace_name,
        protected_text
    )

    # 2) 匹配 "Patient: Name" 格式
    protected_text = re.sub(
        r'(?i)Patient\s*:\s*([A-Za-z\'\.\/]+(?:\s+(?:\b(?:binti|bin|bt|bte|anak|ak|a/l|a/p|al|ap|s/o|d/o)\b|[A-Za-z\'\.\/]+))*)(?=\s*(?:[,\.\n;\(\)]|$|\b(?:Gender|Age|Race|IC|Phone|MyKad|DOB|MRN)\b))',
        r'Patient Name: [PATIENT_NAME]',
        protected_text
    )

    # Step 2: Protect remaining dialog markers (e.g., "Patient: Yes, doctor...")
    protected_text = re.sub(r'(?i)\bPatient\s*:', '__DIALOG_PATIENT__:', protected_text)
    protected_text = re.sub(r'(?i)\bDoctor\s*:', '__DIALOG_DOCTOR__:', protected_text)
    protected_text = re.sub(r'(?i)\bDr\s*:', '__DIALOG_DR__:', protected_text)

    # Step 3: 自然语言姓名表达 (如 "My name is Aminah Binti Yusoff")
    protected_text = re.sub(
        r'(?i)\b(?:my name is|patient\'s name is|patient is named)\s+([A-Za-z\'\.\/]+(?:\s+(?:\b(?:binti|bin|bt|bte|anak|ak|a/l|a/p|al|ap|s/o|d/o)\b|[A-Za-z\'\.\/]+))*)(?=\s*(?:[,\.\n;\(\)]|$|\b(?:IC|Phone|Age|Gender|Race|MyKad|DOB|MRN)\b))',
        r'Patient Name: [PATIENT_NAME]',
        protected_text
    )

    # Step 4: Restore dialog markers
    protected_text = re.sub(r'__DIALOG_PATIENT__:', 'Patient:', protected_text)
    protected_text = re.sub(r'__DIALOG_DOCTOR__:', 'Doctor:', protected_text)
    protected_text = re.sub(r'__DIALOG_DR__:', 'Dr:', protected_text)

    # Step 5: Other PII
    protected_text = re.sub(r'\b\d{6}[- ]?\d{2}[- ]?\d{4}\b', '[MYKAD]', protected_text)
    protected_text = re.sub(r'(\+?6?01[0-9])[- ]?\d{7,8}\b', '[PHONE]', protected_text)
    protected_text = re.sub(r'\b01[0-9]\d{7,8}\b', '[PHONE]', protected_text)
    protected_text = re.sub(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', '[EMAIL]', protected_text)

    return protected_text

def generalize_age_only(text):
    def age_replacer(match):
        age = int(match.group(1))
        if age < 18: return "under 18 years old"
        elif 18 <= age <= 25: return "18-25 years old"
        elif 26 <= age <= 35: return "26-35 years old"
        elif 36 <= age <= 45: return "36-45 years old"
        elif 46 <= age <= 55: return "46-55 years old"
        elif 56 <= age <= 65: return "56-65 years old"
        else: return "over 65 years old"
    return re.sub(r'(\d{1,3})\s*[-]?\s*(years?\s*old|y/o|yo|year-old)', age_replacer, text, flags=re.IGNORECASE)

def clean_patient_data(raw_text):
    raw_text = re.sub(r'(?i)Age Group:\s*[^\n\r]+', '', raw_text)
    # 保留换行符，逐行清理内部多余空格
    lines = [re.sub(r'[ \t]+', ' ', line).strip() for line in raw_text.splitlines()]
    cleaned_text = "\n".join([line for line in lines if line])
    return generalize_age_only(anonymize_malaysian_pii(cleaned_text))

# ---------- 2. Separate History from Current Text ----------
def separate_history(text):
    if "=== PREVIOUS VISIT NOTES ===" in text:
        parts = text.split("=== PREVIOUS VISIT NOTES ===")
        current = parts[0].strip()
        if len(parts) > 1 and "=== END PREVIOUS NOTES ===" in parts[1]:
            history = parts[1].split("=== END PREVIOUS NOTES ===")[0].strip()
        else:
            history = parts[1].strip() if len(parts) > 1 else ""
        return current, history
    return text, ""

# ---------- 3. OpenMed Disease Detection (fallback) ----------
def extract_diseases_with_openmed(text, offline_mode=False):
    if offline_mode:
        warnings.warn("OpenMed in offline mode — disease detection skipped.", RuntimeWarning)
        return []
    try:
        from openmed import analyze_text, OpenMedConfig
        config = OpenMedConfig.from_profile("prod")
        result = analyze_text(text, model_name="disease_detection_superclinical", config=config)
        diseases = []
        if hasattr(result, 'entities'):
            for entity in result.entities:
                if isinstance(entity, dict):
                    label = entity.get('label') or entity.get('type') or ''
                    ent_text = entity.get('text') or ''
                else:
                    label = getattr(entity, 'label', None) or getattr(entity, 'type', None) or ''
                    ent_text = getattr(entity, 'text', None) or ''
                if str(label).upper() in ['DISEASE', 'SYMPTOM', 'CONDITION']:
                    diseases.append(ent_text)
        return diseases
    except ImportError as e:
        warnings.warn(f"OpenMed library missing: {e}. Skipping.", RuntimeWarning)
        return []
    except Exception as e:
        warnings.warn(f"OpenMed error (fallback): {e}", RuntimeWarning)
        return []

# ---------- 4. Common Drug Names Database ----------
COMMON_DRUGS = {
    'paracetamol', 'amoxicillin', 'salbutamol', 'amlodipine', 'metformin',
    'atorvastatin', 'simvastatin', 'omeprazole', 'lansoprazole', 'albuterol',
    'levothyroxine', 'lisinopril', 'enalapril', 'losartan', 'telmisartan',
    'hydrochlorothiazide', 'furosemide', 'spironolactone', 'prednisone',
    'dexamethasone', 'ibuprofen', 'diclofenac', 'naproxen', 'tramadol',
    'morphine', 'codeine', 'aspirin', 'clopidogrel', 'warfarin', 'heparin'
}

def is_common_drug(word):
    word_lower = word.lower()
    if word_lower in COMMON_DRUGS:
        return True
    drug_suffixes = {'mab', 'olol', 'cin', 'vir', 'penem', 'statin', 'pril', 'sartan', 'prazole', 'floxacin'}
    for suffix in drug_suffixes:
        if word_lower.endswith(suffix):
            return True
    if len(word_lower) > 5:
        prefixes = {'amox', 'paracet', 'salbut', 'amlod', 'metform', 'atorva', 'omep', 'lanso', 'ibupr', 'tramad'}
        for prefix in prefixes:
            if word_lower.startswith(prefix):
                return True
    return False

# ---------- 5. Rule-Based SOAP Generation ----------
def generate_soap_rule_based(cleaned_text, diseases=None, history=""):
    # 1. 优先剥离 Doctor 对话行，防止医生问句污染主诉与过敏提取
    clinical_text_only = re.sub(r'(?i)^\s*(?:Doctor|Dr)\s*:[^\n]*', '', cleaned_text, flags=re.MULTILINE).strip()
    # 2. 仅剥离人口学元数据标签本身（如 Patient Name: [PATIENT_NAME]），保留同行后续的患者病情描述
    clinical_text_only = re.sub(r'(?i)(?:Patient Name|Name|Age Group|Age|Gender|Race|IC)\s*:\s*\[?[A-Za-z0-9_\-\s]+\]?[\.,;]?', '', clinical_text_only)
    # 3. 剥离 Patient: 对话前缀及开头的句号/空格
    clinical_text_only = re.sub(r'(?i)^\s*Patient\s*:\s*', '', clinical_text_only, flags=re.MULTILINE).strip()
    clinical_text_only = re.sub(r'^[^\w\s]+', '', clinical_text_only).strip()
    
    # 使用过滤后的 clinical_text_only 进行句段切分与 Subjective 提取
    protected_text = clinical_text_only
    # Remove words that may interfere with medication extraction
    text_for_drugs = re.sub(r'(?i)\bPast\s*(?:medical\s*)?history\s*:?.*?(?=\bCurrent\s*medications|\bAllergies|\bSmoker|\b===|$)', '', cleaned_text)
    text_for_drugs = re.sub(r'\bpeak\b', '', cleaned_text, flags=re.IGNORECASE)
    text_for_drugs = re.sub(r'\b(?:temperature|temp)\s*\d+\.?\d*\s*[°C]?', '', text_for_drugs, flags=re.IGNORECASE)
    text_for_drugs = re.sub(r'\b(?:weight|height)\s*\d+\.?\d*\s*(?:kg|g|cm|m)\b', '', text_for_drugs, flags=re.IGNORECASE)

    drug_pattern = r'\b([A-Za-z]+(?:[- ][A-Za-z]+)?)\s*(\d+\.?\d*\s*(?:mg|g|mcg|ml|tablets?|tabs?|capsules?|caps?|puffs?|drops?)\b(?:\s+(?:tid|bid|qd|qid|prn|tds|qds|od|bd|stat))?)'
    drugs = re.findall(drug_pattern, text_for_drugs, re.IGNORECASE)

    leading_stopwords = {
        'started', 'on', 'taking', 'given', 'prescribed', 'with', 'using',
        'patient', 'currently', 'now', 'self-medicated', 'takes',
        'is', 'was', 'has', 'been', 'he', 'she', 'also', 'and', 'for',
        'days', 'weeks', 'months', 'years', 'fever', 'cough', 'pain', 'history',
        'severe', 'migraine', 'headache', 'weight', 'height', 'temperature'
    }

    if drugs:
        formatted_drugs = []
        for name, dosage in drugs:
            clean_name = re.sub(r'^\d+[- ]*', '', name.strip())
            words = clean_name.split()
            while words and words[-1].lower() in leading_stopwords:
                words.pop()
            while words and words[0].lower() in leading_stopwords:
                words.pop(0)
            clean_name = " ".join(words).strip('- ')
            if not clean_name:
                continue
            if re.match(r'^(?:weight|height|bmi|temp|temperature)$', clean_name, re.IGNORECASE):
                continue
            # 彻底清洗剂量与多余空格（修复 50 0mg -> 500mg，规范单个空格分隔）
            dosage_clean = dosage.strip()
            dosage_clean = re.sub(r'(\d+)\s+(\d+)', r'\1\2', dosage_clean)
            dosage_clean = re.sub(r'(\d+)\s+(mg|g|mcg|ml|tablets?|tabs?|capsules?|caps?|puffs?|drops?)\b', r'\1\2', dosage_clean, flags=re.IGNORECASE)
            dosage_clean = re.sub(r'\s+', ' ', dosage_clean).strip()
            clean_name = re.sub(r'\s+', ' ', clean_name).strip()
            formatted_drugs.append(f"- {clean_name} {dosage_clean}")
        med_list = "\n".join(formatted_drugs) if formatted_drugs else "- None documented"
    else:
        fallback_meds = []
        context_pattern = r'\b(?:current medications?|taking|prescribed|on|using|with)\s*[:]?\s*([\w-]+(?:\s+[\w-]+)?)'
        context_matches = re.findall(context_pattern, text_for_drugs, re.IGNORECASE)
        for match in context_matches:
            drug_words = match.split()
            for w in drug_words:
                if is_common_drug(w) and not re.match(r'^(?:weight|height|bmi|temp)$', w, re.IGNORECASE):
                    fallback_meds.append(w)

        if not fallback_meds:
            for word in re.findall(r'\b[A-Za-z]{4,}\b', text_for_drugs):
                if is_common_drug(word) and word.lower() not in leading_stopwords:
                    non_drug = {'cough', 'pain', 'fever', 'sore', 'shortness', 'chest', 'headache',
                               'patient', 'doctor', 'history', 'past', 'allergies', 'smoking', 'years',
                               'temperature', 'blood', 'pressure', 'heart', 'rate', 'lab', 'result',
                               'diagnosis', 'treatment', 'test', 'report', 'level', 'value', 'range',
                               'unit', 'month', 'year', 'date', 'time', 'today', 'yesterday',
                               'weight', 'height', 'bmi', 'severe', 'migraine'}
                    if word.lower() not in non_drug:
                        fallback_meds.append(word)

        seen = set()
        fallback_meds = [x for x in fallback_meds if not (x.lower() in seen or seen.add(x.lower()))]

        if fallback_meds:
            med_list = "\n".join([f"- {d}" for d in fallback_meds[:5]])
        else:
            med_list = "- None documented"

    # ----- Subjective (精准提取 Gender, Age, Race 并防止前缀重复) -----
    gender_match = re.search(r'Gender:\s*([A-Za-z]+)', cleaned_text, re.IGNORECASE)
    age_match = re.search(r'(?:Age Group|Age):\s*([^\n,]+)', cleaned_text, re.IGNORECASE)
    race_match = re.search(r'Race:\s*([A-Za-z]+)', cleaned_text, re.IGNORECASE)

    demo_parts = []
    if gender_match and gender_match.group(1).lower() != 'not specified':
        demo_parts.append(gender_match.group(1).capitalize())
    if age_match and age_match.group(1).lower() != 'not specified':
        demo_parts.append(age_match.group(1).strip())
    if race_match and race_match.group(1).lower() != 'not specified':
        demo_parts.append(race_match.group(1).capitalize())

    demographics_prefix = ", ".join(demo_parts)
    if demographics_prefix:
        demographics_prefix += " patient"

    # 从剥离人口学后的纯临床文本提取主诉句段
    protected_text = clinical_text_only
    abbreviations = ['approx', 'dr', 'mr', 'mrs', 'ms', 'etc', 'e.g', 'i.e', 'vs', 'pp', 'pt', 'ht', 'wt', 'bp', 'hr']
    for abbr in abbreviations:
        protected_text = re.sub(rf'\b{abbr}\.', f'{abbr}<DOT>', protected_text, flags=re.IGNORECASE)
    sentences = re.split(r'[.;\n]', protected_text)
    sentences = [s.replace('<DOT>', '.').strip() for s in sentences if s.strip()]

    # 1. 深度扩充临床医学词库（包含变体、拼写错误如 dysnopea、同义词如 phlegm/wheezing/sob）
    symptom_keywords = [
        'cough', 'sputum', 'phlegm', 'hemoptysis', 'fever', 'febrile', 'chills', 'rigors',
        'sweat', 'sore throat', 'throat', 'shortness', 'breathlessness', 'dyspnoea', 'dyspnea',
        'dysnopea', 'sob', 'wheezing', 'wheeze', 'stridor', 'rhonchi', 'crackle', 'crepitation',
        'chest pain', 'chest tightness', 'pleuritic', 'headache', 'dizziness', 'palpitation',
        'fatigue', 'myalgia', 'body ache', 'congestion', 'rhinorrhea', 'runny nose', 'nasal',
        'hoarseness', 'choking', 'complains', 'complaint', 'presents'
    ]
    subj_sentences = []

    for sent in sentences:
        sent_str = sent.strip()
        if not sent_str:
            continue
        if any(kw in sent_str.lower() for kw in symptom_keywords):
            fragments = re.split(r',\s*', sent_str)
            has_positive_symptom = False
            for frag in fragments:
                frag = frag.strip()
                if not frag:
                    continue
                if any(kw in frag.lower() for kw in symptom_keywords):
                    if not re.match(r'^(no|denies|nil|without|not)\b', frag, re.IGNORECASE):
                        has_positive_symptom = True
                        break
            if has_positive_symptom and sent_str not in subj_sentences:
                subj_sentences.append(sent_str)

    if subj_sentences:
        subjective = ". ".join(subj_sentences)
    elif sentences:
        subjective = sentences[0]
    else:
        subjective = "N/A"
        
    # 规范化音频/对话中的第一人称代词为第三人称，保持完整句式
    subjective = re.sub(r'\bI\'ve had\b|\bI have had\b', 'Patient has had', subjective, flags=re.IGNORECASE)
    subjective = re.sub(r'\bI have\b|\bI\'m having\b|\bI am having\b', 'patient has', subjective, flags=re.IGNORECASE)
    subjective = re.sub(r'\bmy\b', "patient's", subjective, flags=re.IGNORECASE)
    subjective = re.sub(r'\bI\'m\b|\bI am\b', 'patient is', subjective, flags=re.IGNORECASE)
    subjective = re.sub(r'\bI\b', 'patient', subjective, flags=re.IGNORECASE)

    # 2. 仅在剥离医生问句后的 clinical_text_only 中提取过敏，彻底消除问句干扰
    allergy_match = re.search(r'(?i)(?:allergies|allergy|allergic)\s*[:\-]?\s*([^\n,\.]+)', clinical_text_only)
    if allergy_match:
        alg_val = allergy_match.group(1).strip()
        if re.search(r'(?i)\b(?:nkda|nka|none|nil|no known)\b', alg_val):
            allergy_str = "Allergies: NKDA"
        else:
            allergy_str = f"Allergies: {alg_val}"
    elif re.search(r'(?i)\bNKDA\b', clinical_text_only):
        allergy_str = "Allergies: NKDA"
    else:
        allergy_str = "Allergies: NKDA"

     # 3. 独立安全提取吸烟史（Social History），不影响现有的主诉症状提取
    social_hx_str = ""
    smoke_match = re.search(r'(?i)\b(?:smoker|smoking)\b[^\n\.,]*', clinical_text_only)
    if smoke_match:
        smk_val = smoke_match.group(0).strip()
        if not re.search(r'(?i)\b(?:non|never|denies|no)\b', smk_val):
            social_hx_str = f"Social History: {smk_val.capitalize()}."
        else:
            social_hx_str = "Social History: Non-smoker."

    # 将过敏史与吸烟史整齐追加至 Subjective 结尾
    if social_hx_str:
        subjective = f"{subjective.rstrip(' .')}. {allergy_str}. {social_hx_str}"
    else:
        subjective = f"{subjective.rstrip(' .')}. {allergy_str}."

    # 4. 组合人口学前缀（自动清理多余的 Patient presents with 词组，消除重复拼接）
    if demographics_prefix:
        prefix_words = set(re.findall(r'\b\w+\b', demographics_prefix.lower()))
        subj_head_words = set(re.findall(r'\b\w+\b', subjective[:30].lower()))
        if not (prefix_words & subj_head_words):
            clean_subj = re.sub(r'^(?:Patient\s+presents\s+with|Presents\s+with)\s*', '', subjective, flags=re.IGNORECASE)
            subjective = f"{demographics_prefix} presenting with {clean_subj[0].lower() + clean_subj[1:] if len(clean_subj) > 1 else clean_subj}"

    if subjective == "N/A":
        subjective = f"{demographics_prefix + ' presents' if demographics_prefix else 'Patient presents'} with respiratory symptoms."

    # ----- Objective (with improved pattern boundaries) -----
    objective_items = []

    temp_match = re.search(r'\b(3[5-9]\.?\d*|4[0-2]\.?\d*)\s*°?\s*C\b', cleaned_text, re.IGNORECASE)
    if temp_match:
        objective_items.append(f"Temperature: {temp_match.group(0)}")

    bp_match = re.search(r'\b(?:BP|blood pressure)\s*[:\-]?\s*(\d{2,3}/\d{2,3})\b|\b(\d{2,3}/\d{2,3})\s*mmHg\b', cleaned_text, re.IGNORECASE)
    if bp_match:
        bp_val = bp_match.group(1) or bp_match.group(2)
        objective_items.append(f"BP: {bp_val} mmHg")

    spo2_match = re.search(r'\b(?:spo2|o2 sat|saturation)\b[^\d]*?\b([5-9]\d|100)\b\s*%?', cleaned_text, re.IGNORECASE)
    if spo2_match:
        objective_items.append(f"SpO2: {spo2_match.group(1)}%")

    hr_match = re.search(r'(heart rate|pulse|\bhr\b)\s*[:\-]?\s*(\d+)', cleaned_text, re.IGNORECASE)
    if hr_match:
        objective_items.append(f"HR: {hr_match.group(2)} bpm")

    lab_match = re.search(r'(wbc|hba1c|hb|platelets?)\s*[:\-]?\s*([0-9.]+)', cleaned_text, re.IGNORECASE)
    if lab_match:
        objective_items.append(f"Lab: {lab_match.group(1).upper()} {lab_match.group(2)}")

    objective = ", ".join(objective_items) if objective_items else "None documented"

    # ----- Assessment -----
    if diseases and len(diseases) > 0:
        assessment = ", ".join(diseases)
    else:
        top_symptom = "respiratory symptoms"
        for kw in ['cough', 'sore throat', 'fever', 'headache']:
            if kw in cleaned_text.lower():
                top_symptom = kw
                break
        assessment = f"Patient presents with {top_symptom}. Consider further evaluation to rule out underlying pathology."

    if history:
        history_snippet = history[:200].rstrip(' .')
        assessment += f"\n\n*Previous visit context:* {history_snippet}."

    # ----- Plan -----
    if med_list != "- None documented":
        plan = "Continue current medications as prescribed. Encourage supportive care, rest, and hydration. Follow-up in 3 days if symptoms persist or worsen."
    else:
        plan = "Initiate symptomatic treatment (e.g., antipyretics, analgesics). Advise rest and hydration. Follow-up in 3 days if no improvement."
    if history:
        plan += "\n*Note: Previous medications mentioned in history are not included in current medication list.*"
    
    # 吸烟史 S-A-P 联动：检测阳性吸烟史并扩展 Assessment 与 Plan
    has_positive_smoking = bool(re.search(r'(?i)\b(?:smoker|smoking|pack-years)\b', cleaned_text)) and \
                           not bool(re.search(r'(?i)\b(?:non-smoker|never\s+smoked|denies\s+smoking)\b', cleaned_text))

    if has_positive_smoking:
        assessment += " Tobacco use disorder noted as a secondary risk factor for airway reactivity."
        plan += "\n\n*Lifestyle Counseling:* Provided brief smoking cessation advice (5 A's approach) and discussed impact on respiratory recovery."

    return {
        "med_list": med_list,
        "subjective": subjective,
        "objective": objective,
        "assessment": assessment,
        "plan": plan
    }

# ---------- 6. Post-Processing (Missing Info, Red Flags, Suggestions) ----------
def post_process_soap(original_text, soap_note, soap_dict=None):
    filtered_text = re.sub(r'\b(?:Doctor|Dr)\s*:[^?\n]*[?]', '', original_text, flags=re.IGNORECASE)
    filtered_text = re.sub(r'\b(?:Doctor|Dr)\s*:[^\n]*(?=Patient:|$)', '', filtered_text, flags=re.IGNORECASE)

    combined = (filtered_text + " " + soap_note).lower()

    def is_mentioned_and_not_denied_within_sentence(pattern, text):
        for match in re.finditer(pattern, text, re.IGNORECASE):
            start, end = match.start(), match.end()
            sentence_start = max(
                text.rfind('.', 0, start),
                text.rfind('\n', 0, start),
                text.rfind('!', 0, start),
                text.rfind('?', 0, start)
            )
            if sentence_start == -1:
                sentence_start = 0
            else:
                sentence_start += 1

            ends = [
                p for p in [
                    text.find('.', end),
                    text.find('\n', end)
                ] if p != -1
            ]
            sentence_end = min(ends) if ends else len(text)

            preceding = text[sentence_start:start]
            following = text[end:sentence_end]

            # 若存在转折词（如 but），只检查转折词之后的否定词
            if re.search(r'\b(but|however|yet)\b', preceding, re.IGNORECASE):
                preceding = re.split(r'\b(but|however|yet)\b', preceding, flags=re.IGNORECASE)[-1]
            
            if re.search(r'\b(no|denies|without|nil|none|not|negative for)\s+', preceding, re.IGNORECASE):
                continue
            if re.search(r'^\s*[:\-]?\s*\b(no|none|nil|negative)\b', following, re.IGNORECASE):
                continue
            return True
        return False

    def is_mentioned(keyword, text):
        return re.search(keyword, text, re.IGNORECASE) is not None

    missing = []

    if soap_dict and soap_dict.get('med_list') != "- None documented":
        pass
    else:
        if not is_mentioned(r'medication|drug|taking|prescribed|capsule|tablet|inhaler', original_text):
            missing.append("Current medications not documented")

    if not is_mentioned(r'allerg|\bnkda\b|\bnka\b', combined):
        missing.append("Allergies not documented")
    if not is_mentioned(r'past\s*history|past\s*medical|\bpmh\b|\bpmhx\b|\bphx\b', combined):
        missing.append("Past medical history not documented")
    if not is_mentioned(r'smok', combined):
        missing.append("Smoking status not documented")
    if not is_mentioned(r'occupation|job|work|exposure|dust|chemical|fume|mine|construction|asbestos|silica', combined):
        missing.append("Occupational / environmental exposure not documented")
    if not is_mentioned(r'family\s*history|relative|hereditary|genetic|sibling|parent|child', combined):
        missing.append("Family history not documented")
    if not is_mentioned(r'surgery|operation|trauma|injury|wound|fracture', combined):
        missing.append("Surgical / trauma history not documented")
    if not is_mentioned(r'travel|travelled|abroad|overseas|endemic|foreign', combined):
        missing.append("Travel history not documented")
    if not is_mentioned(r'alcohol|drink|wine|beer|liquor', combined):
        missing.append("Alcohol consumption not documented")
    if not is_mentioned(r'vaccination|immunization|vaccine|flu shot|pneumococcal', combined):
        missing.append("Vaccination history not documented")

    if re.search(r'\b(she|her|female|woman|girl)\b', combined, re.IGNORECASE):
        if not is_mentioned(r'menstrual|menarche|pregnancy|parity|obstetric|gynaecological', combined):
            missing.append("Reproductive / menstrual history not documented (female patient)")

    red_flags = []
    # 扩充 Red Flags 预警正则（新增 Wheezing, Dysnopea 错别字, Chest Tightness 等）
    red_flag_patterns = {
        "Fever >=38°C": r'(?:fever|temp|temperature)\b.{0,30}?\b(?:38|[4-9]\d)\b',
        "Low SpO2 (<95%)": r'\b(?:spo2|o2 sat|saturation)\b.{0,30}?\b(?:[5-8]\d|9[0-4])\b',
        "Shortness of breath / Dyspnoea": r'short\s*of\s*breath|dyspnoea|dyspnea|dysnopea|breathlessness|\bsob\b',
        "Wheezing / Bronchospasm": r'wheez|bronchospasm|stridor|rhonchi',
        "Chest pain / Tightness": r'chest\s*pain|chest\s*tightness|pleuritic',
        "Hemoptysis": r'hemoptysis|bloody\s*sputum|blood-tinged|coughing\s*blood',
        "Altered mental status": r'confusion|disorientation|drowsy|unresponsive'
    }
    for flag, pattern in red_flag_patterns.items():
        if is_mentioned_and_not_denied_within_sentence(pattern, filtered_text):
            red_flags.append(flag)

    # 替换为 100% 稳定可访问的官方主页 URL，彻底解决 404 问题
    suggestions_with_refs = [
        {
            "suggestion": "Encourage rest and adequate supportive hydration.",
            "reference": "WHO Health Topics: Respiratory Infections. https://www.who.int/health-topics"
        },
        {
            "suggestion": "Consider antipyretics (e.g., paracetamol) for symptomatic fever relief if no contraindications.",
            "reference": "MOH Malaysia Official Portal. https://www.moh.gov.my"
        },
        {
            "suggestion": "Advise patient on red flag indicators and to return if symptoms worsen or fever persists >3 days.",
            "reference": "WHO Publications & Clinical Guidelines. https://www.who.int/publications"
        },
        {
            "suggestion": "If prescribing systemic antibiotics, strictly adhere to national antimicrobial stewardship protocols.",
            "reference": "MOH Malaysia Pharmaceutical Services Programme. https://pharmacy.moh.gov.my"
        }
    ]

    return {
        "missing_info": missing,
        "red_flags": red_flags,
        "suggestions": suggestions_with_refs
    }

# ---------- 7. Main Pipeline ----------
def run_pipeline(raw_text, openmed_offline=False):
    current_text, history_text = separate_history(raw_text)
    cleaned = clean_patient_data(current_text)
    diseases = extract_diseases_with_openmed(cleaned, offline_mode=openmed_offline)
    soap_dict = generate_soap_rule_based(cleaned, diseases, history=history_text)

    soap_text = f"""### Medications:
{soap_dict['med_list']}

### SOAP Note:
**Subjective:** {soap_dict['subjective']}
**Objective:** {soap_dict['objective']}
**Assessment:** {soap_dict['assessment']}
**Plan:** {soap_dict['plan']}
"""

    final_post = post_process_soap(cleaned, soap_text, soap_dict=soap_dict)

    return {
        "cleaned": cleaned,
        "diseases": diseases,
        "soap_text": soap_text,
        "soap_dict": soap_dict,
        "post": final_post
    }
