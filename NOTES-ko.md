# ORA 정리 (한국어 메모)

원본: <https://github.com/fast-rua/ORA> (v0.1.0, 2026-11-18 공개, Apache-2.0)
이 메모는 README와 `docs/how-ora-works.md`, `AGENTS.md`, 몇몇 문서를 읽고 쓴 요약이에요.

## 한 줄 요약

**언어 모델이 카메라 화면을 보고 도구를 불러서 로봇을 직접 조종하게 해 주는 틀(harness)**이에요.
로봇용으로 따로 학습한 모델(VLA)이 없고, 로봇 데이터로 학습도 안 하고, 처음 보는 과제를 바로 풀어요(zero-shot).

## 무엇을 하나

- 모델: 기본은 공개 모델 DeepSeek-V4.1-Flash (DeepSeek API). OpenAI, Anthropic, 직접 띄운 서버(vLLM, SGLang 등)도 쓸 수 있어요.
- 환경: 시뮬레이터 벤치마크 5개 + 실제 로봇 1종.
  - LIBERO-Pro, RoboTwin 2.0, RoboDojo, RoboCasa365, RoboLab
  - 실제 로봇: I2RT YAM
- 모델이 보는 것: 카메라 이미지, 로봇 관절 상태, 로봇 설명서(좌표계, 단위), 과제 문장, 쓸 수 있는 도구 목록.
- 모델이 **못** 보는 것: 물체의 정답 위치, 시뮬레이터의 물체 구분 정보, 접촉 정보, 과제 코드, 성공 조건.

## 성적 (DeepSeek-V4.1-Flash, zero-shot)

| 벤치마크 | 로봇 | 케이스 수 | 코드 모드 | 도구 호출 모드 |
|---|---|---:|---:|---:|
| LIBERO-Pro | Franka Panda | 800 | 87.5% | 85.4% |
| RoboTwin 2.0 | 양팔 ALOHA | 235 | 79.6% | 80.4% |
| RoboLab (120개 중 100개 과제) | DROID Franka | 500 | 79.2% | |
| RoboDojo | 양팔 ARX X5 | 198 | 39.9% | |

주의: RoboLab과 RoboDojo는 원래 시뮬레이터 스텝 수 제한이 있어요. **제한 안에서만 세면 RoboLab 67.0%, RoboDojo 10.6%**예요.
위 표는 제한 없이 끝까지 돌린 숫자예요.

다른 숫자들:
- 코드 모드에서 RoboDojo 39개 과제를 공개 모델 6개로 돌리면 48.7%(DeepSeek)부터 5.1%(Nemotron)까지 나와요.
- RoboLab-120에서 Qwen3.8-27B는 혼자 쓰면 5.4%, ORA 안에서 쓰면 52.5%, 추가 학습(PEAR)까지 하면 61.9%예요.

## 어떻게 작동하나

### 세 주체, 각자 하나씩만 결정

| 누구 | 결정하는 것 | 못 하는 것 |
|---|---|---|
| 에이전트 (언어 모델) | 다음에 뭘 보고 뭘 해 볼지 | 시뮬레이터를 직접 바꾸기, 숨은 정보 읽기, 성공 여부 정하기 |
| Supervisor (감독 역할 런타임) | 모델이 낸 동작을 통과시킬지, 어떻게 실행할지 | 모델 대신 다음 동작 고르기 |
| 벤치마크 자체 판정 | 과제 성공 여부 | 모델이 뒤집을 수 없음 |

모델이 "끝났다, 성공했다"라고 말해도 결과는 바뀌지 않아요. 성공은 벤치마크의 원래 검사만 정해요.

### 한 턴의 흐름

1. 모델에게 입력을 줘요: 프롬프트 + 과제 + 도구 목록 + 지금 카메라 화면과 로봇 상태 + 남은 예산.
2. 모델이 도구를 불러요.
3. 도구는 두 종류예요.
   - **보기만 하는 도구**: 상태 보기, 물체 찾기(인식), 3D 계산 등. 로봇은 안 움직여요. 결과를 받고 1번으로 돌아가요.
   - **로봇을 움직이는 도구**: `robot.submit_action` 같은 것. 동작 "제안"이 돼요.
4. Supervisor가 제안을 검사해요(형식, 어떤 화면을 보고 낸 건지, 남은 예산, 환경 한계).
   - 거절되면 이유(공개된 제약만)를 모델에게 돌려주고, 동작은 실행되지 않아요.
   - 통과하면 시뮬레이터가 따로 돌아가는 프로세스에서 실행해요. 경로 계획과 시뮬레이션은 여기서 해요.
5. 실행 결과(실제로 측정된 값)와 새 화면을 모델에게 돌려주고 다음 턴으로 가요.
6. 과제가 끝나거나, 예산이 다 떨어지거나, 모델이 종료를 부르거나, 사람이 멈추면 에피소드가 끝나요.
7. 끝나면 벤치마크가 판정하고, 모든 기록을 해시로 묶어 봉인(seal)해요. 나중에 그대로 다시 보거나 재생할 수 있어요.

### v0.1에서 새로 생긴 것

- **코드 모드**: 모델이 도구를 하나씩 부르는 대신 파이썬 프로그램을 써요. 한 턴에 로봇 동작이 평균 3.6개 들어가요(전에는 1.1개).
- **부드러운 이동**: 모든 이동을 깊이 카메라로 본 장면에 맞춰 경로 계획해요(cuRobo). RoboDojo에서 충돌로 멈춘 이동이 14.1%에서 7.0%로 줄었어요.
- **여러 공개 모델 지원**: 같은 틀에서 모델만 바꿔 끼울 수 있어요.

### 쓰는 도구들

- 물체 찾기: SAM 3
- 잡는 자세 찾기: Contact-GraspNet
- 경로 계획: cuRobo
- 도구 설계 참고: CaP-X, ASPIRE

### 코드 구성 (기술 쪽)

- 모델과 대화하는 부분(agent core)은 TypeScript(`ora-pi/`, Pi 패키지 기반)이에요.
- 로봇 도구, 권한, 동작 규칙은 전부 Python 쪽이에요. 두 부분은 소켓으로 통신해요.
- 시뮬레이터마다 자기 Python 환경에서 따로 돌아가요(`ROBODOJO_PYTHON` 같은 환경 변수로 지정).

| 폴더 | 역할 |
|---|---|
| `ora/app/` | `ora` 명령어 |
| `ora/orchestrator/` | 실행 계획, 에피소드 루프(Supervisor), 판정, 봉인 |
| `ora/agent/` | 도구, 프롬프트 조립, 인식 서비스 |
| `ora-pi/` | 모델 호출 루프(TypeScript), 웹 화면 |
| `ora/environment/` | 시뮬레이터 연결 부분(RoboTwin, LIBERO, RoboDojo, RoboCasa365, RoboLab) |
| `ora/policy/` | 프롬프트, 도구 목록, 로봇 설명서. `policy.lock`으로 고정 |
| `viewer/` | 지난 실행을 보는 뷰어 |
| `tests/` | CPU만으로 도는 테스트 |

## 써 보는 법

필요한 것: Linux, NVIDIA GPU 32GB 이상 1장, sudo 없어도 됨.

```bash
curl -fsSL https://raw.githubusercontent.com/fast-rua/ORA/main/scripts/install.sh | sh
ora setup --env robodojo
ora providers key DEEPSEEK_LOW_API_KEY
ora run --env robodojo --task general_pickup --confirm-launch
```

GPU가 없으면(이 Mac처럼) 가짜 로봇(Synthetic)으로 같은 흐름만 돌려볼 수 있어요: `docs/quickstart.md`.

자주 쓰는 명령:
- `ora watch`: 실행 중이거나 끝난 기록 보기
- `ora web`: 브라우저로 대화와 카메라 보기
- `ora play`: 과제 없이 사람이 직접 지시하기(점수 안 매김)
- `ora-remote`: 내 컴퓨터에서 GPU 서버의 실행을 SSH로 다루기

## 우리 연구(WAM + LLM 에이전트)와 연결해 볼 점

아래는 문서를 읽고 든 생각이에요. 코드로 확인한 건 아니에요.

- **"구조하러 들어가는 LLM 에이전트" 후보**: RoboDojo를 이미 지원해요. DeepSeek 같은 공개 모델이나 직접 띄운 vLLM 서버로 돌릴 수 있어요(`docs/usage/rl-interface.md`).
- **성공 궤적 데이터**: 실행마다 제어 입력 기록(controller journal, NPZ)과 원본 카메라 프레임(PNG)을 남겨요(`docs/developer/authoring/trajectory-capture.md`). WAM 학습 데이터로 바로 쓸 수 있는 형식인지는 확인이 필요해요.
- **주의 1, 중간에 이어받기**: WAM이 실패한 순간의 상태에서 ORA가 이어받는 기능은 문서에서 못 찾았어요. ORA는 "시뮬레이터 전체 상태 저장/복원은 약속하지 않는다"고 써 있어요. 이어받으려면 직접 만들어야 할 수 있어요.
- **주의 2, 사람이 끼어든 실행**: 사람이 메시지를 보낸 실행은 "human-assisted"로 표시되고 자율 성공률에서 빠져요.
- **주의 3, 스텝 제한**: RoboDojo 39.9%는 스텝 제한 없이 센 숫자예요. 제한 안에서는 10.6%예요. 우리 점수와 비교할 때 기준을 맞춰야 해요.

## 회사 vLLM으로 돌리기 (`vllm-endpoint` 브랜치)

원래 ORA는 `https` 주소이거나 같은 기계(`http://127.0.0.1`)의 서버만 받아요.
이 브랜치에서 두 가지를 더했어요.

- `--cluster-http`: 다른 노드의 vLLM(`http://노드이름:8000/v1`)을 받아요. 이 옵션을 붙일 때만 허용돼요. 키가 암호화 없이 오가니 회사 내부망에서만 쓰세요.
- `--reasoning-field reasoning`: vLLM은 생각 내용을 `reasoning` 칸으로 보내요. 이걸 붙여야 생각 내용이 기록되고 다음 턴에 다시 전달돼요.

```bash
# 1) 한 번만: 프로필 등록 (키는 vLLM의 --api-key 값. 키 없이 띄웠으면 아무 값, 예: EMPTY)
./scripts/ora providers add node-vllm --protocol openai_compatible \
    --endpoint http://노드이름:8000/v1 --model deepseek-v4.1-flash --images \
    --cluster-http --reasoning-field reasoning \
    --context-window 131072 --recovery-window 900 --stdin < vllm-key.txt

# 2) 실행
./scripts/ora run --env robodojo --task general_pickup --provider-profile node-vllm --confirm-launch
```

- vLLM이 같은 노드에 있으면 `--endpoint http://127.0.0.1:8000/v1`로 쓰고 `--cluster-http`는 빼도 돼요.
- `--model`은 vLLM의 `--served-model-name`(`/v1/models`에 나오는 이름)과 같아야 해요.
- `--context-window`는 vLLM의 `--max-model-len`과 맞추세요.
- vLLM은 `--enable-auto-tool-choice --tool-call-parser ...`로 띄워야 도구 호출이 돼요.
- 서버에 `HTTP_PROXY`가 설정돼 있으면 `NO_PROXY`에 vLLM 노드 이름을 넣으세요. 등록할 때 연결 확인이 프록시로 새는 걸 막아요.
- 등록하면 바로 연결을 확인해요(응답, 이미지, 도구 호출 왕복). 나중에 다시 확인하려면 `./scripts/ora providers test node-vllm`.

## 더 읽을 곳

- 작동 원리: `docs/how-ora-works.md`, `docs/architecture.md`
- 용어: `docs/glossary.md`
- RoboDojo 설정: `docs/getting-started/robodojo.md`
- 학습 루프 연결: `docs/usage/rl-interface.md`
- 궤적 기록: `docs/developer/authoring/trajectory-capture.md`
- 블로그 글: <https://fastrua.ai/blog/2026-11-code-mode/>
