## SREGym Problems 一览（含注入器内部实现要点）

下表基于 `sregym/conductor/problems` 与各类注入器实现（Virtualization/Application/OTel/Operator/HW/OS/TrainTicket）整理，给出每个问题的注入原理与内部具体操作、Root Cause 位置（仿真/实际），以及 Localization Oracle 期望值。

### 注入器实现细节总览（按家族）
- VirtualizationFaultInjector（K8S 层面，`inject_virtual.py`）
  - Service targetPort 错配：读取 Service JSON，改写 `spec.ports[].targetPort` 并 `kubectl patch service`。
  - 缩容到 0：`kubectl scale deployment <svc> --replicas=0`。
  - 绑不存在节点：抓取 Deployment YAML，设置 `spec.template.spec.nodeSelector={"kubernetes.io/hostname":"extra-node"}`，删除并 `kubectl apply` 修改后的 YAML。
  - PVC claimName 改为不可用：遍历 `spec.template.spec.volumes[].persistentVolumeClaim.claimName += "-broken"`，替换 Deployment。
  - 错误二进制：替换 `containers[].command` 中的二进制名（如将 profile 改为 geo），重发 Deployment。
  - 删除 Service：`kubectl delete service <svc>` 并重启 namespace 内 Pod；恢复用 `/tmp/<svc>_modified.yaml` 重新创建。
  - Resource requests/limits：以传入函数变更 `containers[].resources`，删除并应用修改版，再用 `/tmp/<svc>_modified.yaml` 恢复。
  - 错误 Service selector：读取 Service JSON，将 `spec.selector["current_service_name"]=<svc>`，`patch_service` 生效；恢复时将该键置空。
  - Service DNS 解析失败（按 FQDN）：读 `kube-system/coredns` ConfigMap Corefile，在 `kubernetes` 插件前插入 `template ANY ANY <svc.ns.svc.cluster.local>{ rcode NXDOMAIN }`，`kubectl apply` 后 rollout coredns；恢复时删除该块。
  - 集群级 DNS 失败（stale CoreDNS）：在 Corefile 插入 `template ANY ANY svc.cluster.local { rcode NXDOMAIN }`，并重启 coredns；恢复时删除该块。
  - 错误 DNS Policy：JSON Patch 将 `dnsPolicy=None` 并设置 `dnsConfig.nameservers=["8.8.8.8"]`，rollout 重启并校验 Pod `/etc/resolv.conf`。
  - Sidecar 端口冲突：在 Deployment `containers` 末尾追加 `busybox` sidecar，命令 `nc -lk -p <main_container_port>`，与主容器端口一致。
  - Readiness/Liveness 探针错配：写入 `httpGet: /healthz:8080` 且低 `failureThreshold` 等；Aggressive Liveness 还设置 `terminationGracePeriodSeconds=0`，并可部署自定义慢服务。
  - Duplicate PVC Mounts：创建单一 RWO PVC，强制至少 2 副本且加 `podAntiAffinity`，将该 PVC 同时 mount 至多副本，制造挂载冲突；恢复时转换为 StatefulSet 并使用 `volumeClaimTemplates` 做每副本一卷。
  - PV 亲和性违规：创建绑定 nodeA 的 PV+PVC，同时将 Deployment nodeSelector 指向 nodeB，再挂载该 PVC。
  - Pod 反亲和死锁：设置 `requiredDuringSchedulingIgnoredDuringExecution` 针对同 label，且副本>1，使无法同节点调度。
  - RPC 重试风暴：`kubectl patch configmap <name> -p '{"data":{"GRPC_CLIENT_TIMEOUT":"50ms","GRPC_CLIENT_RETRIES_ON_ERROR":"30"}}'`，按 `-l configmap=<name>` rollout。
  - kube-proxy 镜像替换：获取 DaemonSet YAML，替换 `.spec.template.spec.containers[].image`，apply 并 rollout。
  - Namespace memory 限制：删除关联的 ReplicaSet，创建 `ResourceQuota` `{hard:{memory:<limit>}}`，恢复时删除带 memory 的配额并拉起副本。
  - RBAC 误配：创建 SA/ClusterRole（无 configmaps 权限）/ClusterRoleBinding，将 Deployment 绑定该 SA，并追加会 `kubectl get configmap` 的 initContainer，触发权限错误；恢复删除 RBAC 对象并恢复原 YAML。
  - 滚更策略误配：先部署基础 3 副本，再 `patch` 设置 `rollingUpdate: {maxUnavailable:"100%",maxSurge:"0%"}` 并加挂起 initContainer，rollout。
  - GOGC 降吞吐：对所有 Deployment 使用 JSON Patch 更新/新增 `env.GOGC=10`，rollout；恢复改回 100。
- ApplicationFaultInjector（应用层，`inject_app.py`）
  - 撤销 Mongo 权限/用户：定位 `mongodb-*` Pod，`kubectl exec` 运行 `script/*.sh`（revoke/remove 系列）修改用户/权限，然后删除对应服务 Pod 以强制重连。
  - 错误镜像：将 Deployment 副本设 0，替换 `containers[].image=bad_image` 再恢复副本至 1。
  - 缺失/错误端口环境变量：读取 Deployment，删除或改写指定 `env` 值（如 `PRODUCT_CATALOG_ADDR` 端口），`patch_deployment` 应用。
  - Misconfig App：定向将 `hotel-reserv-geo` 容器镜像改为带缺陷的 `yinfangchen/geo:app3`。
  - Valkey 故障：直接 `valkey-cli CONFIG SET requirepass 'invalid_pass'` 并删除依赖服务 Pod；内存干扰通过提交 Python Job 持续写大值。
- OtelFaultInjector（Feature Flag，`inject_otel.py`）
  - 读取 `flagd-config` ConfigMap 中 `demo.flagd.json`，将指定 `flags[<name>].defaultVariant` 置 `on/off`，更新 ConfigMap 并重启 `flagd` Deployment。
- K8SOperatorFaultInjector（TiDB Operator CR，`inject_operator.py`）
  - 通过 `kubectl apply` 写入 `TidbCluster` CRYAML 注入错误字段（如 `tidb.replicas=100000`、非法 `tolerations.effect`、`podSecurityContext.runAsUser=-1`、`statefulSetUpdateStrategy` 非法、`storageClassName` 不存在）。恢复删除注入 CR 并应用官方 `tidb-cluster.yaml`。
- HWFaultInjector（Khaos eBPF，`inject_hw.py`）
  - 解析目标 Pod 所在节点与容器 ID，借助运行在该节点的 Khaos DaemonSet Pod，在 hostPID 环境下通过 `/proc` 或 cgroup 定位 host PID，执行 `/khaos/khaos <fault_type> <host_pid>` 注入 read() 返回 EIO；恢复对节点执行 `--recover`。
- RemoteOSFaultInjector（远程 OS，`inject_remote_os.py`）
  - 读取 `scripts/ansible/inventory.yml`，SSH 到每个 worker，下发 `/tmp/kill_kubelet.sh` 并后台执行持续 `pkill -TERM kubelet`，记录 PID；恢复时读取 PID 并杀掉脚本，清理文件。
- TrainTicketFaultInjector（Flagd，`inject_tt.py`）
  - 修改 `flagd-config` ConfigMap 的 `flags.yaml`，将 `flags[<fault>].defaultVariant` 置 `on/off`，重启 `flagd`，等待 20s 生效。

| 问题名（类名/文件名） | 注入原理与具体操作 | Root Cause 位置（仿真/实际） | Localization Oracle（expected） |
| --- | --- | --- | --- |
| AdServiceFailure (`ad_service_failure.py`) | OTel：修改 `flagd-config.demo.flagd.json` 将 `adFailure` 的 `defaultVariant=on`，重启 `flagd` | 仿真：特性标记；实际：`ad` 代码/依赖 | 有：`OtelLocalizationOracle`，expected=`"ad"` |
| AdServiceHighCpu (`ad_service_high_cpu.py`) | OTel：将 `adHighCpu` 置 on，触发高 CPU 逻辑 | 仿真：特性标记；实际：资源使用 | 有：`OtelLocalizationOracle`，expected=`"ad"` |
| AdServiceManualGc (`ad_service_manual_gc.py`) | OTel：将 `adManualGc` 置 on，触发 GC 逻辑 | 仿真：特性标记；实际：GC 策略 | 有：`OtelLocalizationOracle`，expected=`"ad"` |
| CartServiceFailure (`cart_service_failure.py`) | `OtelFaultInjector.inject_fault("cartFailure")` 使 `cart` 服务失败 | 仿真：应用特性标记；实际：`cart` 服务代码/逻辑问题 | 有：`OtelLocalizationOracle`，expected=`"cart"` |
| ImageSlowLoad (`image_slow_load.py`) | `OtelFaultInjector.inject_fault("imageSlowLoad")` 让前端图片加载变慢 | 仿真：应用特性标记；实际：`frontend` 代码/依赖/IO 性能 | 有：`OtelLocalizationOracle`，expected=`"frontend"` |
| KafkaQueueProblems (`kafka_queue_problems.py`) | `OtelFaultInjector.inject_fault("kafkaQueueProblems")` 模拟 Kafka 消息队列异常 | 仿真：应用特性标记；实际：Kafka 组件/队列配置/吞吐瓶颈 | 有：`OtelLocalizationOracle`，expected=`"kafka"` |
| LoadGeneratorFloodHomepage (`loadgenerator_flood_homepage.py`) | `OtelFaultInjector.inject_fault("loadGeneratorFloodHomepage")` 让 loadgenerator 洪泛首页请求 | 仿真：负载发生在 loadgenerator 配置；实际：外部流量突增/压测导致前端过载 | 有：`OtelLocalizationOracle`，expected=`"frontend"` |
| PaymentServiceFailure (`payment_service_failure.py`) | `OtelFaultInjector.inject_fault("paymentFailure")` 使 `payment` 服务失败 | 仿真：应用特性标记；实际：`payment` 服务代码/依赖失败 | 有：`OtelLocalizationOracle`，expected=`"payment"` |
| PaymentServiceUnreachable (`payment_service_unreachable.py`) | `OtelFaultInjector.inject_fault("paymentUnreachable")` 使 `checkout` 调用 `payment` 不可达 | 仿真：应用特性标记；实际：网络/服务发现/路由问题 | 有：`OtelLocalizationOracle`，expected=`"checkout"` |
| ProductCatalogServiceFailure (`product_catalog_failure.py`) | `OtelFaultInjector.inject_fault("productCatalogFailure")` 使 `product-catalog` 服务失败 | 仿真：应用特性标记；实际：`product-catalog` 服务代码/依赖失败 | 有：`OtelLocalizationOracle`，expected=`"product-catalog"` |
| RecommendationServiceCacheFailure (`recommendation_service_cache_failure.py`) | `OtelFaultInjector.inject_fault("recommendationCacheFailure")` 模拟缓存故障 | 仿真：应用特性标记；实际：`recommendation` 服务缓存/后端依赖 | 有：`OtelLocalizationOracle`，expected=`"recommendation"` |
| MissingService (`missing_service.py`) | 删除 Service：`kubectl delete service <svc>`；备份到 `/tmp/<svc>_modified.yaml`；重启全部 Pod 强制生效 | 仿真：Service 对象缺失；实际：误删/漏发 | 有：`LocalizationOracle`，expected=`[faulty_service]` |
| WrongServiceSelector (`wrong_service_selector.py`) | 读取 Service JSON，设置 `spec.selector.current_service_name=<svc>` 干扰选择器后 `patch_service` | 仿真：Service 配置错误；实际：selector/标签漂移 | 有：`LocalizationOracle`，expected=`[faulty_service]` |
| WrongDNSPolicy (`wrong_dns_policy.py`) | JSON Patch：`dnsPolicy=None` 且 `dnsConfig.nameservers=["8.8.8.8"]`，rollout 并校验 Pod `/etc/resolv.conf` | 仿真：Pod 规格错误；实际：`dnsPolicy`/dnsConfig | 有：`LocalizationOracle`，expected=`[faulty_service]` |
| ServiceDNSResolutionFailure (`service_dns_resolution_failure.py`) | 在 coredns Corefile 插入对 `service.ns.svc.cluster.local` 的 `template ANY ANY`，返回 NXDOMAIN，重启 coredns | 仿真：DNS 破坏；实际：CoreDNS/Service/DNS | 有：`LocalizationOracle`，expected=`[faulty_service]` |
| StaleCoreDNSConfig (`stale_coredns_config.py`) | 在 Corefile 插入 `template ANY ANY svc.cluster.local`（匹配所有服务域）并返回 NXDOMAIN | 仿真：CoreDNS 配置漂移；实际：CoreDNS ConfigMap | 有：`LocalizationOracle`，expected=`["coredns"]` |
| SidecarPortConflict (`sidecar_port_conflict.py`) | 向 Deployment 追加 sidecar：`busybox:latest` 执行 `nc -lk -p <主容器端口>`，与主容器端口冲突 | 仿真：端口冲突；实际：容器/sidecar 配置 | 有：`LocalizationOracle`，expected=`[faulty_service]` |
| IngressMisroute (`ingress_misroute.py`) | 直接调用 K8s API 修改 Ingress，将路径路由到错误 backend | 仿真：Ingress 规则错误；实际：Ingress 规则/控制器配置 | 有：`LocalizationOracle`，expected=`[correct_service]` |
| NetworkPolicyBlock (`network_policy_block.py`) | 创建 `deny-all` NetworkPolicy 阻断目标服务入/出站流量 | 仿真：NetworkPolicy 配置封锁；实际：NetworkPolicy/Calico/CNI 规则 | 有：`LocalizationOracle`，expected=`[faulty_service]` |
| K8STargetPortMisconfig (`target_port.py`) | Service JSON 改写 `spec.ports[].targetPort: 9090→9999` 并 `patch_service`，致与 Pod 端口不匹配 | 仿真：端口映射错误；实际：Service `targetPort` | 有：`LocalizationOracle`，expected=`["user-service"]` |
| DuplicatePVCMounts (`duplicate_pvc_mounts.py`) | 创建单一 RWO PVC，并强制多副本共享该 PVC + podAntiAffinity，改 YAML 后重发 Deployment | 仿真：卷挂载错误；实际：卷/调度策略 | 有：`LocalizationOracle`，expected=`[faulty_service]` |
| PVCClaimMismatch (`pvc_claim_mismatch.py`) | 将 `volumes[].persistentVolumeClaim.claimName += "-broken"`，替换 Deployment，Pods Pending | 仿真：PVC 绑定错；实际：PVC/PV/SC | 有：`LocalizationOracle`，expected=`[mongodb-* 列表]` |
| PersistentVolumeAffinityViolation (`persistent_volume_affinity_violation.py`) | 创建仅绑定 nodeA 的 PV+PVC，同时将 Deployment nodeSelector 指向 nodeB 并挂载该 PVC | 仿真：PV 亲和违规；实际：PV/调度 | 有：`LocalizationOracle`，expected=`[faulty_service]` |
| PodAntiAffinityDeadlock (`pod_anti_affinity_deadlock.py`) | 设置严格 `requiredDuringScheduling` 的 `podAntiAffinity`，且副本>1，阻止调度 | 仿真：反亲和死锁；实际：亲和/反亲和 | 有：`LocalizationOracle`，expected=`[faulty_service]` |
| RBACMisconfiguration (`rbac_misconfiguration.py`) | `VirtualizationFaultInjector._inject("rbac_misconfiguration")` 破坏 initContainer/服务所需 RBAC | 仿真：RBAC 权限不足；实际：Role/RoleBinding/ServiceAccount 配置 | 有：`LocalizationOracle`，expected=`[faulty_service]` |
| MissingConfigMap (`missing_configmap.py`) | `VirtualizationFaultInjector._inject("missing_configmap")` 移除关键 ConfigMap | 仿真：ConfigMap 丢失；实际：ConfigMap 未创建/误删/版本不兼容 | 有：`LocalizationOracle`，expected=`[faulty_service]` |
| ConfigMapDrift (`configmap_drift.py`) | 从容器内 `cat .../config.json` 取原 JSON，删除关键键（如 `GeoMongoAddress`），生成新 ConfigMap 并通过 JSON Patch mount 到容器路径，rollout | 仿真：配置漂移；实际：配置变更不一致 | 有：`LocalizationOracle`，expected=`["geo"]` |
| LivenessProbeMisconfiguration (`liveness_probe_misconfiguration.py`) | 为容器写入错误 `livenessProbe`（/healthz:8080，failureThreshold=1）并将 `terminationGracePeriodSeconds=0` | 仿真：探针错误；实际：`livenessProbe` | 有：`LocalizationOracle`，expected=`[faulty_service]` |
| LivenessProbeTooAggressive (`liveness_probe_too_aggressive.py`) | 部署慢服务脚本，设置 `livenessProbe` 0-1s 周期+低阈值，促使频繁重启 | 仿真：阈值过低；实际：探针参数 | 有：`LocalizationOracle`，expected=`["aux-service"]` |
| ReadinessProbeMisconfiguration (`readiness_probe_misconfiguration.py`) | 写入错误 `readinessProbe`（/healthz:8080，低阈值等），替换 Deployment | 仿真：探针错误；实际：`readinessProbe` | 有：`LocalizationOracle`，expected=`[faulty_service]` |
| ResourceRequestTooLarge (`resource_request.py`) | `VirtualizationFaultInjector._inject("resource_request", duration=set_memory_limit)` 将 `requests.memory` 调到超过节点容量 | 仿真：资源请求超配；实际：Pod 资源请求配置 | 有：`LocalizationOracle`，expected=`[faulty_service]` |
| ResourceRequestTooSmall (`resource_request.py`) | `set_memory_limit` 设置过小的 `limits.memory`（如 `10Mi`） | 仿真：资源限制过小；实际：Pod 资源限制配置 | 有：`LocalizationOracle`，expected=`[faulty_service]` |
| NamespaceMemoryLimit (`namespace_memory_limit.py`) | `injector.inject_namespace_memory_limit()` 对 `search` 增加较小内存限制触发限制/OOM | 仿真：命名空间资源策略收紧；实际：`ResourceQuota/LimitRange` 等策略配置 | 有：`LocalizationOracle`，expected=`["search"]` |
| ScalePodSocialNet (`scale_pod.py`) | `VirtualizationFaultInjector._inject("scale_pods_to_zero")` 将 `user-service` 副本缩到 0 | 仿真：副本数配置错误；实际：Deployment `replicas` 配置/弹性策略 | 有：`LocalizationOracle`，expected=`["user-service"]` |
| AssignNonExistentNode (`assign_non_existent_node.py`) | `VirtualizationFaultInjector._inject("assign_to_non_existent_node")` 配置到不存在的节点 | 仿真：调度约束无效；实际：nodeSelector/affinity/tolerations 等错误 | 有：`LocalizationOracle`，expected=`["user-service"]` |
| TaintNoToleration (`taint_no_toleration.py`) | 对所有工作节点打 `NoSchedule` taint，并给 Deployment 打不匹配的 toleration，然后删 Pod 促使重排 | 仿真：节点污点/容忍度不匹配；实际：节点/Pod 调度策略配置 | 有：`LocalizationOracle`，expected=`["user-service"]` |
| WrongBinUsage (`wrong_bin_usage.py`) | `VirtualizationFaultInjector._inject("wrong_bin_usage")` 使用错误可执行/入口 | 仿真：容器入口/命令错误；实际：镜像 `command/args` 或路径配置 | 有：`LocalizationOracle`，expected=`["profile"]` |
| IngressMisroute（见上） | 同上 | 同上 | 同上 |
| NetworkPolicyBlock（见上） | 同上 | 同上 | 同上 |
| FaultyImageCorrelated (`faulty_image_correlated.py`) | 使用 `ApplicationFaultInjector.inject_incorrect_image()` 将多服务镜像替换为错误镜像（HotelReservation 多服务） | 仿真：镜像版本/仓库错误；实际：CI/CD 镜像标签/签名/回滚问题 | 有：`LocalizationOracle`，expected=`[frontend, geo, profile, rate, recommendation, reservation, user, search]` |
| UpdateIncompatibleCorrelated (`update_incompatible_correlated.py`) | 将多 MongoDB 服务镜像升级为不兼容版本（如 `mongo:8.0.14-rc0`） | 仿真：不兼容升级；实际：数据库主从/驱动/版本语义不匹配 | 有：`LocalizationOracle`，expected=`[mongodb-* 列表]` |
| IncorrectImage (`incorrect_image.py`) | 将 `product-catalog` 镜像替换为错误镜像 | 仿真：镜像配置错误；实际：镜像标签/镜像缺陷 | 有：`LocalizationOracle`，expected=`["product-catalog"]` |
| IncorrectPortAssignment (`incorrect_port_assignment.py`) | 在 `checkout` 部署设置错误的 `PRODUCT_CATALOG_ADDR` 端口（8082 而非 8080） | 仿真：环境变量端口错误；实际：服务间联通端口/配置约定 | 有：`LocalizationOracle`，expected=`["checkout"]` |
| MisconfigAppHotelRes (`misconfig_app.py`) | `ApplicationFaultInjector._inject("misconfig_app")` 注入 HotelReservation 中 `geo` 的应用层配置错误 | 仿真：应用配置错误；实际：应用配置文件/启动参数/环境变量 | 有：`LocalizationOracle`，expected=`["geo"]` |
| MissingEnvVariable (`missing_env_variable.py`) | 删除 `frontend` 的关键环境变量（如 `CART_ADDR`） | 仿真：环境变量缺失；实际：配置下发缺失/模版渲染错误 | 有：`LocalizationOracle`，expected=`["frontend"]` |
| EnvVariableShadowing (`env_variable_shadowing.py`) | 注入同名环境变量遮蔽正确配置（`frontend-proxy`） | 仿真：变量遮蔽；实际：配置层优先级/覆盖策略错误 | 有：`LocalizationOracle`，expected=`["frontend-proxy"]` |
| ReadinessProbeMisconfiguration（见上） | 同上 | 同上 | 同上 |
| LivenessProbeMisconfiguration（见上） | 同上 | 同上 | 同上 |
| RollingUpdateMisconfigured (`rolling_update_misconfigured.py`) | `VirtualizationFaultInjector._inject("rolling_update_misconfigured")` 错配滚更策略到 `custom-service` | 仿真：更新策略错误；实际：Deployment `strategy`/`maxUnavailable` 等参数 | 有：`LocalizationOracle`，expected=`["custom-service"]` |
| ResourceRequestTooLarge/TooSmall（见上） | 同上 | 同上 | 同上 |
| NamespaceMemoryLimit（见上） | 同上 | 同上 | 同上 |
| WorkloadImbalance (`workload_imbalance.py`) | 将 `kube-proxy` DaemonSet 镜像替换为非官方版本并扩大前端副本，施加突增流量，制造负载不均 | 仿真：集群网络组件异常；实际：CNI/kube-proxy 异常、节点不均衡 | 有：`LocalizationOracle`，expected=`["frontend", "kube-proxy"]` |
| KubeletCrash (`kubelet_crash.py`) | `RemoteOSFaultInjector.inject_kubelet_crash()` 远程使节点 kubelet 进程崩溃并触发服务滚更 | 仿真：节点/OS 层面服务崩溃；实际：节点内 kubelet/系统服务/内核问题 | 无（代码注释说明后续可考虑加入） |
| ReadError (`read_error.py`) | `HWFaultInjector` 基于 eBPF 在目标节点为所有 Pod 的 `read()` 注入 EIO (-5) | 仿真：硬件/内核级 I/O 故障；实际：磁盘/控制器/内核 I/O 子系统 | 有（注入后设置）：`LocalizationOracle`，expected=`[target_node]` |
| CapacityDecreaseRPCRetryStorm (`capacity_decrease_rpc_retry_storm.py`) | 修改 RPC 超时/重试配置（ConfigMap）造成重试风暴并配合压测 | 仿真：RPC 配置错误；实际：超时/重试/回退策略错误 | 有：`LocalizationOracle`，expected=`["rpc"]` |
| LoadSpikeRPCRetryStorm (`load_spike_rpc_retry_storm.py`) | 同上但采用更高并发的负载场景 | 同上 | 有：`LocalizationOracle`，expected=`["rpc"]` |
| GCCapacityDegradation (`gc_capacity_degradation.py`) | 通过设置 `GOGC=10` 等方式降低 GC 吞吐能力，并运行工作负载评估 | 仿真：运行时 GC 参数不当；实际：应用 GC/内存管理策略 | 有：`LocalizationOracle`，expected=`["garbage collection"]` |
| TrainTicketF17 (`trainticket_f17.py`) | `TrainTicketFaultInjector._inject("fault-17-nested-sql-select-clause-error")` 注入 SQL 语句错误 | 仿真：应用代码/SQL 逻辑错误；实际：`ts-voucher-service` 查询实现/SQL | 有：`LocalizationOracle`，expected=`["ts-voucher-service"]` |
| TrainTicketF22 (`train_ticket_f22.py`) | `..._inject("fault-22-sql-column-name-mismatch-error")` 注入列名不匹配 | 仿真：应用/Schema 不匹配；实际：`ts-contacts-service` 与 DB schema 不一致 | 有：`LocalizationOracle`，expected=`["ts-contacts-service"]` |
| MongoDBAuthMissing (`auth_miss_mongodb.py`) | `VirtualizationFaultInjector._inject("auth_miss_mongodb")` 取消 MongoDB 身份认证 | 仿真：存储认证缺失；实际：Mongo 用户/权限配置 | 有：`LocalizationOracle`，expected=`["url-shorten-mongodb"]` |
| MongoDBRevokeAuth (`revoke_auth.py`) | `ApplicationFaultInjector._inject("revoke_auth")` 撤销 Mongo 用户权限/认证 | 仿真：权限撤销；实际：DB 账户/权限管理 | 有：`LocalizationOracle`，expected=`[faulty_service]` |
| MongoDBUserUnregistered (`storage_user_unregistered.py`) | `ApplicationFaultInjector._inject("storage_user_unregistered")` 使 Mongo 用户未注册/不可用 | 仿真：用户不存在；实际：用户初始化/迁移缺失 | 有：`LocalizationOracle`，expected=`[faulty_service]` |
| ValkeyAuthDisruption (`valkey_auth_disruption.py`) | `ApplicationFaultInjector._inject("valkey_auth_disruption")` 破坏 Valkey 认证 | 仿真：缓存鉴权失败；实际：Valkey 用户/密码/ACL 配置 | 有：`LocalizationOracle`，expected=`"valkey-cart"` |
| ValkeyMemoryDisruption (`valkey_memory_disruption.py`) | `ApplicationFaultInjector._inject("valkey_memory_disruption")` 干扰 Valkey 内存 | 仿真：内存不足/淘汰策略异常；实际：实例内存/参数设置 | 有：`LocalizationOracle`，expected=`"valkey"` |
| RollingUpdateMisconfigured（见上） | 同上 | 同上 | 同上 |
| Operator: OverloadReplicas (`operator_misoperation/overload_replicas.py`) | `K8SOperatorFaultInjector.inject_overload_replicas()` 将 TiDB 集群副本数极大化使系统不健康 | 仿真：运维误操作（Operator CR 副本策略错误）；实际：TiDB Operator CR/Deployment 配置 | 有：`LocalizationOracle`，expected=`["tidb-cluster"]` |
| Operator: NonExistentStorage (`operator_misoperation/non_existent_storage.py`) | `...inject_non_existent_storage()` 指定不存在的 StorageClass | 仿真：存储类配置错误；实际：Operator CR 中的 `storageClassName` 配置 | 有：`LocalizationOracle`，expected=`["tidb-cluster"]` |
| Operator: InvalidAffinityToleration (`operator_misoperation/invalid_affinity_toleration.py`) | `...inject_invalid_affinity_toleration()` 设置无效 `toleration`/affinity | 仿真：调度策略误配；实际：Operator CR 的容忍/亲和配置 | 有：`LocalizationOracle`，expected=`["tidb-cluster"]` |
| Operator: SecurityContextFault (`operator_misoperation/security_context_fault.py`) | `...inject_security_context_fault()` 设置非法 `runAsUser` 等 | 仿真：安全上下文误配；实际：`securityContext`（Operator CR） | 有：`LocalizationOracle`，expected=`["tidb-cluster"]` |
| Operator: WrongUpdateStrategy (`operator_misoperation/wrong_update_strategy.py`) | `...inject_wrong_update_strategy()` 使用不当更新策略 | 仿真：滚更策略误配；实际：更新策略（Operator CR） | 有：`LocalizationOracle`，expected=`["tidb-cluster"]` |

说明：
- “仿真”表示本框架通过注入器（Virtualization/Application/Otel/Operator/OS/HW）合成/模拟该类故障；“实际”给出了真实生产环境下此类问题的根因归属。
- 若未特别说明，Mitigation Oracle 见各实现文件相应 `self.mitigation_oracle` 的绑定。


