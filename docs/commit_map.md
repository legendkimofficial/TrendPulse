# 커밋 번호 대응표 (작성자 이메일 수정, 2026-11-17)

GitHub이 개인 이메일 때문에 push를 거절해(GH007) `gdelt-gkg` 브랜치에서 `0b76b80`(origin/main) 이후 커밋 29개의
author/committer 이메일을 GitHub noreply 주소로 바꿨다. 이름, 작성 시각(author date, committer date), 메시지, 파일 내용은
그대로이며 커밋 번호만 바뀌었다. `main`과 `0b76b80` 이전 커밋은 그대로다.

- 방법: `git filter-branch --env-filter`(옛 이메일과 같을 때만 바꿈), 범위 `0b76b80..gdelt-gkg`.
- 확인: 새 범위에 옛 이메일 0건, `gdelt-gkg-backup`(로컬 백업, 옛 커밋)과 파일 트리 동일, 시각·메시지 동일, 전체 테스트 143개 통과.
- `experiments/results/gkg/` 의 JSON·summary.md 에 적힌 커밋 번호(예: `0340e48`, `f19ff63`)는 **수정 전 번호**다. 결과 파일은 고치지 않았으니 아래 표로 찾는다.

| 수정 전 | 수정 후 | 작성일 | 메시지 |
|---|---|---|---|
| `d6504f9` | `ec03523` | 2026-11-16 | Add GDELT GKG downloader and Claude Code rules |
| `e384c2b` | `adfd44e` | 2026-11-16 | Update Claude Code rules and pre-register E2 events |
| `b1b8b9c` | `9dbfbd1` | 2026-11-16 | Add GKG slim stream reader, key rules and input manifest |
| `d5a01ba` | `e7ce345` | 2026-11-16 | Record GKG input manifest for 2024-10-01 |
| `2e438d7` | `ddf3e76` | 2026-11-16 | Require word boundary for every outlet self-name match |
| `92f62d5` | `afb0a67` | 2026-11-16 | Rebuild GKG input manifest at 2e438d7 |
| `5ed6b46` | `d1fe362` | 2026-11-16 | Add GKG time-decay engine and sqlite evidence store |
| `1ed6127` | `8afee94` | 2026-11-16 | Record GKG engine run and manifest for 2024-10-01 |
| `181f4f0` | `188db96` | 2026-11-16 | Correct the batch-timing note in GKG summary limitations |
| `07b6ffb` | `5946501` | 2026-11-16 | Drop country-name org keys; cap evidence postings at 30 per key per day |
| `384edfc` | `838bf59` | 2026-11-16 | Record GKG manifest and engine run for 2024-10-01 after country-org and evidence-cap changes |
| `43b1966` | `1a53f94` | 2026-11-16 | Add P3 detection: 72h new issues, 30-day burst/steady, gap holds |
| `c1c7a11` | `cbdea8b` | 2026-11-16 | Record GKG P3 detection run for 2024-10-01 and its limitations |
| `be571f2` | `32174d1` | 2026-11-16 | Set burst F=1.5 with 21-day warm-up and a 7-day new-issue warm-up |
| `4f53170` | `5d87e86` | 2026-11-17 | Add GKG dashboard (app_gkg.py, port 5002) with precomputed snapshot cache |
| `4f4cf5f` | `512ba78` | 2026-11-17 | Add GKG README section, submission doc and 30-day dashboard cache record |
| `1ee22f7` | `c4afca3` | 2026-11-17 | Split the 30-day tab into rising / cooling / steady groups |
| `809eebc` | `ab811ce` | 2026-11-17 | Record 30-day dashboard cache with three-group 30-day tab |
| `7056399` | `71f6fe3` | 2026-11-17 | Add GKG experiment runner (E1-E5) with --quick and --only |
| `9d0ad3f` | `01156c2` | 2026-11-17 | Record GKG experiments --quick run (2024-10-01) |
| `0340e48` | `d325729` | 2026-11-17 | Correct window count in the experiment memory estimate line |
| `bf019b9` | `5537f5b` | 2026-11-17 | Record full GKG experiment run E1-E5 (2024-09-15..2024-11-14) |
| `c58900d` | `cd3fe8d` | 2026-11-17 | Rebuild the GKG dashboard on the HuffPost v0.4 screen (gkg-0.4, cache v4) |
| `90ffc30` | `fb8a0ec` | 2026-11-17 | Add post-hoc revised E2 rule and start-of-run provenance to the runner |
| `f19ff63` | `415da0d` | 2026-11-17 | Record --quick --only E2 check (2024-10-01) with the revised rule |
| `b14e6c4` | `fab12fa` | 2026-11-17 | Rerun E2 from a clean commit with the post-hoc revised rule |
| `c20cc48` | `59af1f5` | 2026-11-17 | Document post-hoc E2 rule, 57-article gap, E3/E4 review, 61-day cache |
| `798699e` | `e242fff` | 2026-11-17 | Record decisions: keep U+2028 gap and both Valencia results; P5/P7 not done |
| `b9e7b8e` | `86f8073` | 2026-11-17 | Let the GKG agent use any OpenAI-compatible API via LLM_* env vars |

전체 해시:

| 수정 전 | 수정 후 |
|---|---|
| `d6504f9a89e8c47390c41a5a1d4b2f200dd8ba57` | `ec035236713f75d6811032d2fe899c25d39a9f86` |
| `e384c2ba2c3ed6ded761abbfad96648d754cccfb` | `adfd44e15dccd25611f0bd5586283409c5764910` |
| `b1b8b9cac12b6cce6d3c6ee036dfe9ee6e44ba5e` | `9dbfbd163cc5a332edfead66948e1225c28ee3bc` |
| `d5a01baf130c9ea84cd3e8ee97f6068ac9b5f548` | `e7ce34549c35713441ce806c48a283cb2ab20999` |
| `2e438d7bd9fab35cbaaca15429418c2fc097f582` | `ddf3e7648f559c9a6f4cc143ffb01cd0440b5b84` |
| `92f62d558ad24034d30d9c1fd42e78cbae37be18` | `afb0a67bd57440b7f31c8e06460d801381e34ffe` |
| `5ed6b463aefae93e26a7c3327dd3df79f2c66d50` | `d1fe3627e6a4c47ab131562753e5473665c1f292` |
| `1ed612765df6a043072c81faa91a8e3d10ec64ee` | `8afee949649a056cdee07b4feb6812b32ee9b5f0` |
| `181f4f0174d139ca7f281c22d1ade064933e816c` | `188db9675e6a4cd161df059f964073afff6ade20` |
| `07b6ffb29d7ceb4ac9227e0461bdc04ccba30f35` | `5946501f171184972dab6f1efafbecf98b72347e` |
| `384edfcb1d18c72b6dc3f1a69572df61bd304ef9` | `838bf59ccff9e552167acda775d8a08af302b847` |
| `43b1966dca5906da7ece872b2668b4917b20a989` | `1a53f942f7cf4a16697fd161cf43a823b5c82a07` |
| `c1c7a11695d8da6c7597bbf5800e42be7d719ab3` | `cbdea8b83aaac8bc4817629002217f8e6108dc29` |
| `be571f20e15fd916c16d04566410f0f08763541b` | `32174d17369d6f34891d3431d8b4405dc40ca5ef` |
| `4f53170850250bb33adb2836b894ba22db07d8a5` | `5d87e8694e8902a3380f5fe512d951b94bcc1f35` |
| `4f4cf5f38ff2d7a40aa1dcb325bf962dd7aacce3` | `512ba7803e3261ca8a0f0c43bf35736c314d1b95` |
| `1ee22f7174d56cc74c95a88fa9e2ecd4bd4737e6` | `c4afca30f786c414c334c7cb4a6f79c1d6696190` |
| `809eebc0612d2c5493e1f3bb3cf9db4f767b5aec` | `ab811ce3abc28ffbe62eb6cf89456f3153c55a4d` |
| `70563998281a2e535a9e2f5677dc3212776684a9` | `71f6fe342ec0e17cb17e04f0e172f224b9fb5c5c` |
| `9d0ad3f46094fc0d57a85d70d11fce335bcd8274` | `01156c209749f7ac5197e36cdc4f23542d45fa7f` |
| `0340e4805929bfb653333ca018c19280b45e8e1f` | `d325729196c1ba263611d938fab2d59548807352` |
| `bf019b98a6c5f12fb52de7f25fb5aa312c79fc8a` | `5537f5b8a9f81c8fa59f512f9d9fd371dc6ad871` |
| `c58900d3791464ca20b558f6fbca16812f72061f` | `cd3fe8dce09fee93b0017cc1ba91ec70ad68f4a9` |
| `90ffc30a6f75ce287193328da32aa3b418e4d47a` | `fb8a0ec9471a19af2fdefdc6b71c13b29c934aeb` |
| `f19ff63a9179f2ac8c4f705a8618b691f8b7a538` | `415da0d76abc81710473a974477ec3b62d545be9` |
| `b14e6c4425ca077aed784af605f13277f9d26070` | `fab12fab6dbe017fa02c678c372655cf97b38a1d` |
| `c20cc48dbf9e4e68a36868630cce0833e01ba7fd` | `59af1f5cc151ba92efaea283ab8166712763fc6b` |
| `798699ee16ab4c9cf056536244dec1568d376a34` | `e242fff081d3a4d8af0c32f8533b2bf4bcbbb60e` |
| `b9e7b8e04993cf1478db1063a71c2165f1085e6f` | `86f8073f4a9e4b0a2d4ddd74b4da75bf652fb179` |
