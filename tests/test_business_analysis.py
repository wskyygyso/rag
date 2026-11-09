"""确定性业务约束摘要和报告兜底测试。"""

from app.business_analysis import build_deterministic_findings
from app.llm_diagnosis import build_evidence_context
from app.reporting import build_assessment


def _context(start_line: int, lines: list[str]) -> str:
    """把测试源码行转换为 Worker 保存的带行号上下文字符串。

    参数:
        - `start_line`：第一行的真实源码行号，类型为 `int`。
        - `lines`：从该行开始的源码内容，类型为 `list[str]`。

    Returns:
        `行号: 内容` 形式的上下文，供确定性分析器按真实行号建立证据引用。
    """
    return "\n".join(
        f"{start_line + index}: {content}"
        for index, content in enumerate(lines)
    )


def _usd_fuse_evidence() -> list[dict]:
    """构造覆盖美元熔断关键守卫的精简源码证据。

    Returns:
        与真实 waky6 文件路径和关键行语义一致的证据集合，测试不需要依赖
        本机额外挂载的业务仓库即可验证报告闭环。
    """
    return [
        {
            "file": "php/services/Monitor/Fuse/UsdFuseService.php",
            "line": 34,
            "context": _context(
                8,
                [
                    " * 覆盖美元兑代理币与官方提现两类消费场景；两者只阻断业务入口，不产生可恢复的资产记录。",
                    "class UsdFuseService extends BaseFuseService",
                    "{",
                    "    const KEY_PREFIX = 'usd_fuse';",
                    "    const OFFICIAL_PAYMENT = 1;",
                    "    const USD_EXCHANGE = 2;",
                    "    public function getFuseTypes(): array",
                    "    {",
                    "        return [FuseTypes::FUSE_TYPE_CONSUME];",
                    "    }",
                    "    public static function getSourceMap(): array",
                    "    {",
                    "        return [",
                    "            self::OFFICIAL_PAYMENT => '熔断官方提现',",
                    "            self::USD_EXCHANGE => '熔断美元兑代理币',",
                    "        ];",
                    "    }",
                    "    public function isTriggerMaxThreshold(int $uid, int $type, $num): array",
                    "    {",
                    "        return [false, 0];",
                    "    }",
                    "    public function getRestoreService()",
                    "    {",
                    "        return null;",
                ],
            ),
        },
        {
            "file": "php/services/Monitor/Fuse/UsdFuseService.php",
            "line": 155,
            "context": _context(
                142,
                [
                    "    /** 读取指定美元来源的熔断标识；Redis 异常按放行处理并记录日志。 */",
                    "    private static function isTriggered(int $uid, int $source): bool",
                    "    {",
                    "        if ($uid <= 0) {",
                    "            return false;",
                    "        }",
                    "        try {",
                    "            return (new FuseService())->checkFuseFlag($uid, FuseTypes::BIG_TYPE_USD, FuseTypes::FUSE_TYPE_CONSUME, $source);",
                    "        } catch (\\Throwable $e) {",
                    "            \\ExceptionGuard::log(\\ExceptionGuard::logger(), $e, __METHOD__);",
                    "            return false;",
                    "        }",
                    "    }",
                    "    public function handelRecover(int $uid, int $bigType, int $type, int $monitorIgnoreStat, array $content): array",
                    "    {",
                    "        return $this->returnFail('usd business fuse does not support asset recovery');",
                    "    }",
                ],
            ),
        },
        {
            "file": "php/services/Monitor/Fuse/BaseFuseService.php",
            "line": 62,
            "context": _context(
                58,
                [
                    "    public function formatCacheKey(): string",
                    "    {",
                    "        $key = $this->getKey();",
                    "        return sprintf($this->hashFormatKey, $key, $this->dataStructure);",
                    "    }",
                    "    public function checkFuseFlag(array $fuseFlags): bool",
                    "    {",
                    "        $cacheKey = $this->formatCacheKey();",
                    "        $all = $this->redis->hgetall($cacheKey);",
                ],
            ),
        },
        {
            "file": "php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php",
            "line": 87,
            "context": _context(
                86,
                [
                    "        // 官方打款命中美元熔断时直接拒绝；币商渠道不受影响。",
                    "        if ($orderType === self::ORDER_TYPE_OFFICIAL && UsdFuseService::isOfficialPaymentTriggered($uid)) {",
                    "            throw new WithdrawalException(WithdrawalException::OFFICIAL_WITHDRAWAL_FUSE_TRIGGERED);",
                    "        }",
                    "        if ($orderType === self::ORDER_TYPE_DEALER && DiamondFuseService::isWithdrawToProxyTriggered($uid, $region)) {",
                    "            throw new WithdrawalException(WithdrawalException::DEALER_WITHDRAWAL_FUSE_TRIGGERED);",
                    "        }",
                ],
            ),
        },
        {
            "file": "php/domain/Withdrawal/Exception/WithdrawalException.php",
            "line": 71,
            "context": _context(
                70,
                [
                    "    /** 官方提现命中美元熔断，需引导用户改用币商渠道或稍后重试。 */",
                    "    public const OFFICIAL_WITHDRAWAL_FUSE_TRIGGERED = 17;",
                ],
            ),
        },
        {
            "file": "php/services/Diamond/WithdrawalChannelConfigService.php",
            "line": 70,
            "context": _context(
                69,
                [
                    "        // 美元熔断只隐藏官方历史回填，币商历史渠道继续按原规则返回。",
                    "        if ($isOfficial && $officialFuseTriggered) {",
                    "            return [];",
                    "        }",
                    "        $isOfficialGroup = $group === WithdrawalChannelService::GROUP_OFFICIAL;",
                    "        // 美元熔断只阻断官方打款，币商渠道必须继续参与目录组装。",
                    "        if ($isOfficialGroup && $officialFuseTriggered) {",
                    "            continue;",
                    "        }",
                ],
            ),
        },
        {
            "file": "php/action/CoinsMerchantControl.php",
            "line": 1731,
            "context": _context(
                1730,
                [
                    "        // 美元兑代理币命中来源级熔断时停止兑换。",
                    "        if (UsdFuseService::isUsdExchangeTriggered($uid)) {",
                    "            \\Application::stop(",
                    "                CoinsMerchantConf::getText('ERR_USD_EXCHANGE_FUSE_TRIGGERED', 'coins_merchant'),",
                    "                CoinsMerchantConf::ERR_USD_EXCHANGE_FUSE_TRIGGERED",
                    "            );",
                    "        }",
                ],
            ),
        },
        {
            "file": "php/conf/CoinsMerchantConf.php",
            "line": 33,
            "context": _context(
                33,
                ["    const ERR_USD_EXCHANGE_FUSE_TRIGGERED = 20018; // 美元兑代理币命中熔断"],
            ),
        },
        {
            "file": "php/lang/zh/coins_merchant.php",
            "line": 58,
            "context": _context(
                58,
                ["    'ERR_USD_EXCHANGE_FUSE_TRIGGERED' => '美元兑换暂不可用，请稍后重试',"],
            ),
        },
    ]


def _diamond_withdrawal_evidence() -> list[dict]:
    """构造覆盖钻石提现金额、换算、渠道分支和手续费公式的源码证据。

    Returns:
        与真实 waky6 文件路径和关键行语义一致的证据集合。多段 context 模拟
        Worker 对不同检索词分别扩展相邻源码后再合并的结果。
    """
    return [
        {
            "file": "php/action/DiamondControl.php",
            "line": 59,
            "context": _context(
                57,
                [
                    "        try {",
                    "            return (new \\Domain\\Withdrawal\\Service\\WithdrawalOrderCreateService())->create($uid, $input, $request);",
                    "        } catch (\\Domain\\Withdrawal\\Exception\\WithdrawalException $e) {",
                ],
            ),
        },
        {
            "file": "php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php",
            "line": 29,
            "context": _context(
                27,
                [
                    " */",
                    "class WithdrawalOrderCreateService",
                    "{",
                ],
            ),
        },
        {
            "file": "php/config/diamonds.php",
            "line": 82,
            "context": _context(
                78,
                [
                    "     * max_diamonds=0、daily_limit=0 均表示不限。自然日按用户手机时区计算。",
                    "     */",
                    "    'withdrawal_limits' => [",
                    "        'multiple' => 10000,",
                    "        'dealer' => [",
                    "            'min_diamonds' => 10000,",
                    "        ],",
                    "        'official' => [",
                    "            'min_diamonds' => getByTestMode(80000,800000),",
                    "            'max_diamonds' => getByTestMode(4000000,4000000),",
                    "            'daily_limit' => getByTestMode(5,5),",
                    "        ],",
                ],
            ),
        },
        {
            "file": "php/config/diamonds.php",
            "line": 32,
            "context": _context(
                30,
                [
                    "        // 钻石提现：1$ = 40000 钻石",
                    "        'dollar_to_diamond' => 40000,",
                ],
            ),
        },
        {
            "file": "php/conf/SysEnv.php",
            "line": 122,
            "context": _context(
                120,
                [
                    "    function getByTestMode($testModeVal, $noTestModeVal)",
                    "    {",
                    "        return env('TEST_MODE', 0) ? $testModeVal : $noTestModeVal;",
                    "    }",
                ],
            ),
        },
        {
            "file": "php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php",
            "line": 116,
            "context": _context(
                113,
                [
                    "        $income = self::calcIncome($diamonds);",
                    "",
                    "        // 7. 官方渠道在下单时冻结费率和三方渠道快照，后续配置调整不影响历史单",
                    "        $official = $orderType === self::ORDER_TYPE_OFFICIAL",
                    "            ? self::buildOfficialSnapshot($channel['config'], $income)",
                    "            : null;",
                    "        $fee = $official['fee'] ?? '0.00';",
                    "        $payoutMoney = $official['net'] ?? $income;",
                ],
            ),
        },
        {
            "file": "php/domain/Withdrawal/Service/WithdrawalOrderCreateService.php",
            "line": 423,
            "context": _context(
                417,
                [
                    "        // 3. 先把用户提现的美元金额换算成三方结算币种金额，并统一保留两位小数。",
                    "        $grossCurrency = round((float)$grossUsd * $rate, 2);",
                    "",
                    "        // 4. 手续费 = 结算金额 × 比例费率 + 固定手续费，同样保留两位小数。",
                    "        $fee = round($grossCurrency * $feeRate + $feeFixed, 2);",
                    "",
                    "        // 5. 三方实际打给收款人的净额 = 结算金额 - 手续费。",
                    "        $net = round($grossCurrency - $fee, 2);",
                    "",
                    "        // 6. 净额必须大于 0；否则说明固定费或比例费已吞掉全部金额，该渠道不可下单。",
                    "        if ($net <= 0) {",
                    "            throw new WithdrawalException(WithdrawalException::OFFICIAL_SETTLEMENT_AMOUNT_INVALID, [], [",
                ],
            ),
        },
        {
            "file": "php/config/anchorPayment.php",
            "line": 1402,
            "context": _context(
                1397,
                [
                    "    'OFFICIAL' => [",
                    "        'USDT' => [",
                    "            [",
                    "                'supported_regions' => ['global'],",
                    "                'unsupported_countries' => [],",
                    "                'name' => 'USDT-TRC20',",
                    "                'img_url' => 'uc/img/TRON.png',",
                    "                'pay_fee' => 0.01,",
                    "                'additional_pay_fee' => 2.5,",
                    "                'pay_rate' => 1,",
                    "                'currency' => 'USDT',",
                    "            ],",
                ],
            ),
        },
        {
            "file": "php/config/anchorPayment.php",
            "line": 1430,
            "context": _context(
                1427,
                [
                    "        'Epay-USD' => [",
                    "            [",
                    "                'supported_regions' => ['global'],",
                    "                'name' => 'Epay',",
                    "                'img_url' => 'uc/img/epay.png',",
                    "                'pay_fee' => 0.01,",
                    "                'additional_pay_fee' => 0.5,",
                    "                'pay_rate' => 1,",
                    "                'currency' => 'USD',",
                    "            ],",
                    "        ],",
                ],
            ),
        },
    ]


def _diamond_exchange_evidence() -> list[dict]:
    """构造钻石兑金币配置与兑换方法守卫的源码证据。

    Returns:
        与真实 waky6 路径和关键行一致的证据集合，用于验证系统在没有人工新增
        “钻石兑金币”领域映射时，仍能把动态发现到的配置和服务方法解释成业务答案。
    """
    return [
        {
            "file": "php/config/diamonds.php",
            "line": 9,
            "context": _context(
                3,
                [
                    "/**",
                    " * 钻石兑金币（向币商）配置",
                    " */",
                    "return [",
                    "    'diamond_exchange' => [",
                    "        'diamond_to_coin' => getByTestMode(2, 2),",
                    "        'min_diamonds' => 10000,",
                    "        'multiple' => 10000,",
                    "        'max_diamonds' => 0,",
                    "        'gears' => [10000, 50000, 100000],",
                    "    ],",
                    "];",
                ],
            ),
        },
        {
            "file": "php/domain/DiamondExchange/Service/DiamondExchangeService.php",
            "line": 299,
            "context": _context(
                292,
                [
                    "    /**",
                    "     * 发起钻石兑金币",
                    "     */",
                    "    public function exchange(int $uid, int $diamonds, int $merchantUid): array",
                    "    {",
                    "        $conf = Config::get('diamonds.diamond_exchange');",
                    "        $ratio = (int)$conf['diamond_to_coin'];",
                    "        if ($diamonds < (int)$conf['min_diamonds'] || $diamonds % (int)$conf['multiple'] !== 0) {",
                    "            \\Application::stop(Lang::get('INVALID_PARAMS', 'exception'), 10003);",
                    "        }",
                ],
            ),
        },
    ]


def _spaceship_fragment_evidence() -> list[dict]:
    """构造覆盖 2025 年度金主飞船商品碎片规则的源码证据。

    Returns:
        与 waky6 配置、普通奖励计算公式、随机惊喜发奖和领奖互斥规则对应的
        精简证据集合；用于验证既能给出等级总量，也能解释公式执行顺序。
    """
    config_file = (
        "waky6_miyouactivity/crontab/ceremony2025GoldOwner/config.php"
    )
    level_contexts = []
    level_values = (
        (1, 4000, "[1, 5, 100]"),
        (2, 8000, "[1, 5, 150]"),
        (3, 12000, "[1, 5, 200]"),
        (4, 16000, "[1, 5, 250]"),
    )
    for level, fragments, fixed in level_values:
        start_line = 151 + (level - 1) * 50
        lines = [
            f"'id' => {level},",
            "'ordinary_prizes' => [",
            "//商品碎片数",
            f"'fragments' => {fragments},",
            "'top_fixed' => [",
            f"{fixed},",
        ]
        if level >= 3:
            lines.append(f"[6, 10, {100 if level == 3 else 150}],")
        lines.extend(
            [
                "],",
                "'minimum_guarantee_rate' => 60,",
                "'surprise_rate' => 40,",
                "1 => [10, 30],",
                "2 => [20, 50],",
                "3 => [30, 20]",
            ]
        )
        level_contexts.append(
            {
                "file": config_file,
                "line": start_line + 1,
                "context": _context(start_line, lines),
            }
        )
    return [
        *level_contexts,
        {
            "file": "waky6_miyouactivity/crontab/ceremony2025GoldOwner/SpaceshipService.php",
            "line": 435,
            "context": _context(
                435,
                [
                    "$fragments = intval(($item[1] - $item[0] + 1) * $item[2]);",
                    "$surplus = max(0, $fragments - $topFixed);",
                    "$minimum = max(0, intval($ordinaryPrizes['minimum_guarantee_rate'] * $surplus / 100));",
                    "$averageMinimum = max(0, intval(($minimum) / $averageUsers));",
                    "if ($averageMinimum <= 0) {",
                    "$totalMinimum = $averageMinimum * $averageUsers;",
                    "$minimumSur = max(0, $minimum - $totalMinimum);",
                    "$surprise = intval($ordinaryPrizes['surprise_rate'] * $surplus / 100);",
                    "$sur = max(0, $surplus - $minimum - $surprise) + $minimumSur;",
                    "$rowSurpriseUser = intval($userRate * $totalUsers / 100);",
                    "$rowFragments = max(1, $rowSurpriseUser > 0 ? intval($rowSurprise / $rowSurpriseUser) : 0);",
                ],
            ),
        },
        {
            "file": "waky6_miyouactivity/crontab/ceremony2025GoldOwner/SurprisePrize.php",
            "line": 95,
            "context": _context(
                95,
                [
                    "$randomIndexes = $this->generateRandomIndexes($totalUsers, $surpriseUsers);",
                    "$indexes = range(1, $totalUsers);",
                    "shuffle($indexes);",
                    "return array_slice($indexes, 0, $count);",
                    "$surpriseData[] = ['rank_id' => $randomIndexes[$currentIndex], 'fragments' => $rowFragments, 'level_id' => $levelId];",
                ],
            ),
        },
        {
            "file": "waky6_miyouactivity/crontab/ceremony2025GoldOwner/SpaceshipOrdinaryPrize.php",
            "line": 97,
            "context": _context(
                97,
                [
                    "$returnData['top_fragments'] = $this->getTopRankFragments();",
                    "if ($returnData['top_fragments'] <= 0) {",
                    "$returnData['minimum_guarantee_fragments'] = $this->getMinimumGuarantee();",
                    "}",
                    "$returnData['surprise_fragments'] = $this->getSurprise();",
                ],
            ),
        },
        {
            "file": "waky6_miyouactivity/crontab/ceremony2025GoldOwner/SpaceshipConfig.php",
            "line": 130,
            "context": _context(
                130,
                [
                    "if ($level > $maxLevel) {",
                    "$cfg = $levels[$maxLevel];",
                    "$cfg['id'] = $level;",
                    "$cfg['value'] = $value;",
                    "return $cfg;",
                ],
            ),
        },
    ]


def _frontend_diamond_display_evidence() -> list[dict]:
    """构造 waky4 钱包页钻石展示的完整后端证据链。

    Returns:
        覆盖路由、登录、主播门槛、钱包余额、公会区域、档位配置和响应字段的
        源码上下文。路径和行号与真实 waky4 仓库保持一致。
    """
    return [
        {
            "file": "waky4_miyouphp/php/config/auth.php",
            "line": 88,
            "context": _context(
                86,
                [
                    "        'Action/HotGame.upWallet',//热游小游戏",
                    "        'Action/Diamond.exchange',//110钱包页跳转，绕过验签",
                    "        'Action/Diamond.getDiamondGearList',//110钱包页跳转，绕过验签",
                    "        'Action/Diamond.record',//110钱包页跳转，绕过验签",
                ],
            ),
        },
        {
            "file": "waky4_miyouphp/php/action/DiamondControl.php",
            "line": 21,
            "context": _context(
                21,
                [
                    "    public static function getDiamondGearList($request)",
                    "    {",
                    "        $data = [];",
                    "        $uid = \\Request::CheckLogin();",
                    "",
                    "        //用户是否是主播",
                    "        $anchor = (new \\UserModel())->isAnchor($uid);",
                    "        if (!$anchor) {",
                    "            $data['is_show'] = 0;",
                    "            return $data;",
                    "        }",
                    "",
                    "        //获取用户钻石余额",
                    "        $diamondModel = new \\Model\\DiamondModel();",
                    "        $userDiamonds = $diamondModel->getUserDiamonds($uid);",
                    "        $data['diamonds'] = $userDiamonds['diamonds'] ?? 0;",
                    "",
                    "        $anchorModel = new AnchorModel();",
                    "        $region = $anchorModel->getAnchorRegion($uid);",
                    "",
                    "        //获取档位",
                    "        $diamondGears = $diamondModel->getDiamondGears($region);",
                    "        $diamondGears = array_map(function ($diamondGear) {",
                    "            $diamondGear['id'] = intval($diamondGear['id']);",
                    "            $diamondGear['diamond_num'] = intval($diamondGear['diamond_num']);",
                    "            $diamondGear['coin_num'] = intval($diamondGear['coin_num']);",
                    "            return $diamondGear;",
                    "        }, $diamondGears);",
                    "        $data['diamond_gears'] = $diamondGears;",
                    "",
                    "        return $data;",
                    "    }",
                ],
            ),
        },
        {
            "file": "waky4_miyouphp/php/action/PropControl.php",
            "line": 76,
            "context": _context(
                76,
                [
                    "    public function getMyRebateCard($request)",
                    "    {",
                    "        $uid = \\Request::CheckLogin();",
                    "        $rebateCard = (new PropModel(getMainDB()))->getMaxScaleRebateCard($uid);",
                    "",
                    "        $nobleSvc = new \\Services\\noble\\NobleService;",
                    "        $nobleLv  = $nobleSvc->batchGetNobleLevel([$uid]);",
                    "",
                    "        $rebateCard['nobleLv'] = intval($nobleLv[$uid] ?? 0);",
                    "",
                    "        $rebateCard['bagType'] = (new ChargeGiftBagModel())->getEffectiveGiftBagType($uid); //490，钱包页展示限时礼包",
                    "",
                    "        $rebateCard['is_anchor'] = (new \\UserModel())->isAnchor($uid);   //110，是否主播，前端展示钻石档位和用户钻石",
                    "",
                    "        return $rebateCard;",
                    "    }",
                ],
            ),
        },
        {
            "file": "waky4_miyouphp/php/model/DiamondModel.php",
            "line": 114,
            "context": _context(
                114,
                [
                    "    public function getUserDiamonds($uid): array",
                    "    {",
                    "        $sql = \"select uid, diamonds,consume from hsing_pay.t_user_diamonds where uid = ?\";",
                    "        $row = $this->slaveDb->query_one($sql, $uid);",
                    "        if (!$row) {",
                    "            return ['diamonds' => 0, 'consume' => 0];",
                    "        }",
                    "        return ['diamonds' => intval($row['diamonds']), 'consume' => intval($row['consume'])];",
                    "    }",
                    "",
                    "    public function getDiamondGears($region): array",
                    "    {",
                    "        $config = (new RebateDiamondExchangeConfig($region))->getJsonConfig();",
                    "        $result = [];",
                    "",
                    "        foreach($config[RebateDiamondExchangeConfig::LEVELS] as $i => $row)",
                    "        {",
                    "            $result[] = [",
                    "                'id' => $i,",
                    "                'diamond_num' => $row[RebateDiamondExchangeConfig::DIAMOND_NUM],",
                    "                'coin_num' => $row[RebateDiamondExchangeConfig::COIN_NUM],",
                    "            ];",
                    "        }",
                    "",
                    "        return $result;",
                    "    }",
                ],
            ),
        },
        {
            "file": "waky4_miyouphp/php/model/usermodel.php",
            "line": 1756,
            "context": _context(
                1756,
                [
                    "    // 是否主播",
                    "    public function isAnchor($uid)",
                    "    {",
                    "        return (new TAnchor())->where('uid',$uid)->where('status',0)->exists();",
                    "    }",
                ],
            ),
        },
        {
            "file": "waky4_miyouphp/php/model/AnchorModel.php",
            "line": 998,
            "context": _context(
                998,
                [
                    "    public function getAnchorRegion($uid)",
                    "    {",
                    "        $query = $this->slaveDB->query_one('select b.region from data_analyse.t_guild_anchor a inner join data_analyse.t_guild b on a.gid=b.gid where b.status=? and a.uid=?', self::GUILD_STATUS_NORMAL, $uid);",
                    "",
                    "        if (false === isset($query['region']))",
                    "            return 'global';",
                    "",
                    "        return $query['region'];",
                    "    }",
                ],
            ),
        },
        {
            "file": "waky4_miyouphp/php/services/AnchorSettlement/BaseAnchorConfig.php",
            "line": 22,
            "context": _context(
                18,
                [
                    "    const TYPE_REBATE_SETTLEMENT = 6;//结算后返金币政策",
                    "    const TYPE_REBATE_DAILY = 7;  //每日返金币政策",
                    "    const TYPE_REBATE_DIAMOND = 8;    //主播收礼返回钻石政策",
                    "    const TYPE_REBATE_DIAMOND_EXCHANGE_COIN = 9;//主播钻石兑金币政策",
                    "    const TYPE_REBATE_SETTLEMENT_DIAMOND = 10;  //主播结算钻石政策",
                ],
            ),
        },
        {
            "file": "waky4_miyouphp/php/services/AnchorSettlement/BaseAnchorConfig.php",
            "line": 72,
            "context": _context(
                72,
                [
                    "        $db = \\ServiceFactory::getSlaveDB();",
                    "        $conf = $db->query_one('select json_conf from data_analyse.t_anchor_settlement_config where type=? and region=?', $this->type, $this->region);",
                    "",
                    "        if(true === empty($conf['json_conf']))",
                    "            return false;",
                    "",
                    "        $json = json_decode($conf['json_conf'], true);",
                    "",
                    "        if(true === empty($json))",
                    "            return false;",
                    "            ",
                    "        $this->readJsonConf = $json;",
                    "        return $this->readJsonConf;",
                ],
            ),
        },
        {
            "file": "waky4_miyouphp/php/services/AnchorSettlement/RebateDiamondExchangeConfig.php",
            "line": 8,
            "context": _context(
                8,
                [
                    "class RebateDiamondExchangeConfig extends BaseAnchorConfig",
                    "{",
                    "    const LEVELS = 'levels';",
                    "    const DIAMOND_NUM = 'diamondNum'; //钻石数量",
                    "    const COIN_NUM = 'coinNum'; //可兑换金币数量",
                    "    function __construct($region = 'ar')",
                    "    {",
                    "        parent::__construct($region);",
                    "        $this->type = self::TYPE_REBATE_DIAMOND_EXCHANGE_COIN;",
                    "    }",
                ],
            ),
        },
    ]


def test_usd_fuse_question_produces_deterministic_business_limits() -> None:
    """验证美元熔断问题能输出来源、入口、错误码、隔离和故障策略。"""
    result = {"evidence": _usd_fuse_evidence()}
    findings = build_deterministic_findings(
        "waky6项目 排查触发美元熔断时，业务有哪些具体限制？",
        result,
    )
    categories = {item["category"] for item in findings["facts"]}
    assert {
        "作用范围",
        "单笔阈值",
        "官方提现",
        "官方渠道",
        "币商提现",
        "美元兑换",
        "隔离范围",
        "资产恢复",
        "故障策略",
    } <= categories
    statements = " ".join(item["statement"] for item in findings["facts"])
    assert "20018" in statements
    assert "错误码为 17" in statements
    assert "UID" in statements
    assert findings["evidence_refs"]


def test_diamond_exchange_question_produces_amount_answer_without_domain_mapping() -> None:
    """验证通用检索发现配置和方法后，可直接回答钻石兑金币的整数倍规则。"""
    result = {"evidence": _diamond_exchange_evidence()}
    findings = build_deterministic_findings(
        "钻石兑金币的整数倍是多少？",
        result,
    )
    categories = {item["category"] for item in findings["facts"]}
    assert {"兑换粒度", "最低兑换", "换算比例", "单次上限"} <= categories
    statements = " ".join(item["statement"] for item in findings["facts"])
    assert "10,000 钻石的整数倍" in statements
    assert "2 钻石兑换 1 金币" in statements
    assert "当前配置未设置单次钻石兑金币上限" in statements
    assert "10,000 钻石的整数倍" in findings["conclusion"]
    assert findings["evidence_refs"]


def test_frontend_diamond_display_question_explains_complete_backend_chain() -> None:
    """验证钱包页钻石问题会解释展示门槛、余额、区域和档位来源。"""
    result = {"evidence": _frontend_diamond_display_evidence()}
    findings = build_deterministic_findings(
        "waky4 排查前端展示钻石是根据什么？",
        result,
    )
    categories = {item["category"] for item in findings["facts"]}
    assert {
        "页面入口",
        "钱包页显隐",
        "展示条件",
        "余额来源",
        "区域来源",
        "档位配置",
        "响应字段",
        "读取一致性",
    } <= categories
    statements = " ".join(item["statement"] for item in findings["facts"])
    assert "getDiamondGearList" in statements
    assert "Prop.getMyRebateCard" in statements
    assert "前端依据该字段展示钻石档位和用户钻石" in statements
    assert "status = 0" in statements
    assert "非主播只返回 `is_show = 0`" in statements
    assert "hsing_pay.t_user_diamonds.diamonds" in statements
    assert "t_anchor_settlement_config" in statements
    assert "type = 9" in statements
    assert "diamond_gears[].diamond_num/coin_num" in statements
    assert "主从复制延迟" in statements
    assert "getDiamondGearList" in findings["conclusion"]
    assert "Prop.getMyRebateCard" in findings["conclusion"]
    assert "is_show=0" in findings["conclusion"]
    assert "hsing_pay.t_user_diamonds.diamonds" in findings["conclusion"]
    assert findings["evidence_refs"]
    assert any("客户端渲染源码" in item for item in findings["uncertainties"])
    result["deterministic_findings"] = findings
    assessment = build_assessment(result)
    assert len(assessment["confirmed_facts"]) == len(findings["facts"])
    assert all("代码证据：" not in item for item in assessment["confirmed_facts"])


def test_diamond_withdrawal_question_produces_amount_and_fee_answer() -> None:
    """验证钻石提现问题直接给出渠道下限、换算、官方手续费和币商边界。"""
    result = {"evidence": _diamond_withdrawal_evidence()}
    findings = build_deterministic_findings(
        "waky6项目 钻石提现最小兑换金额是多少？有手续费嘛？",
        result,
    )
    categories = {item["category"] for item in findings["facts"]}
    assert {
        "业务入口",
        "提现粒度",
        "提现下限",
        "美元换算",
        "官方金额与频次",
        "手续费承担",
        "手续费公式",
        "渠道费率",
    } <= categories
    statements = " ".join(item["statement"] for item in findings["facts"])
    assert "币商渠道最低 10,000 钻石" in statements
    assert "正式环境最低 800,000 钻石" in statements
    assert "测试环境最低 80,000 钻石" in statements
    assert "1 美元 = 40,000 钻石" in statements
    assert "USDT-TRC20 为 1% + 固定 2.5" in statements
    assert "Epay 为 1% + 固定 0.5" in statements
    assert "币商订单的手续费字段默认为 0.00" in statements
    assert "不能与旧版主播工资提现混用" in statements
    assert "币商最低 10,000 钻石" in findings["conclusion"]
    assert "官方正式环境最低 800,000 钻石" in findings["conclusion"]
    assert "USDT-TRC20 为 1%+2.5 USDT" in findings["conclusion"]
    assert findings["uncertainties"]


def test_diamond_withdrawal_report_keeps_answer_without_ai() -> None:
    """验证关闭或失败 AI 时，钻石提现报告仍保留金额和手续费结论。"""
    result = {
        "evidence": _diamond_withdrawal_evidence(),
        "warnings": ["代码证据不足，已跳过 AI 诊断叙写"],
        "validation": {},
    }
    result["deterministic_findings"] = build_deterministic_findings(
        "钻石提现最小兑换金额是多少？有手续费嘛？",
        result,
    )
    assessment = build_assessment(result)
    assert "800,000" in assessment["conclusion"]
    assert "手续费" in assessment["conclusion"]
    assert "无法形成可靠诊断结论" not in assessment["conclusion"]
    assert "未定位到相关代码证据" not in assessment["unverified_items"]


def test_old_anchor_withdrawal_uses_current_repository_chain_when_new_service_absent() -> None:
    """验证新版提现文件不存在时会解释当前旧链路，而不是引用失效映射。"""
    evidence = [
        {
            "file": "waky6_miyouphp/php/services/Anchor/AdvanceServices.php",
            "line": 272,
            "context": _context(
                272,
                [
                    "    public function checkAnchorCanWithdraw(string $uid, int $diamonds, int $to_uid): bool",
                    "    {",
                    "        $region = $anchorModel->getAnchorRegion($uid);",
                    "        $diamondsPrice = \\Conf\\Config::get('anchor.diamond_to_usd');",
                    "        $minMoney = $region == 'tr' ? 9 : 10;",
                    "        $money = sprintf('%.2f', $diamonds / $diamondsPrice);",
                    "        if (filter_var($money, FILTER_VALIDATE_FLOAT, ['options' => ['min_range' => 0.01]]) === false) {",
                    "            \\Application::stop('LESS_THAN_MINMONEY', 34567);",
                    "        }",
                    "        if ($to_uid && $money < $minMoney) {",
                    "            \\Application::stop('LESS_THAN_MINMONEY', 34567);",
                    "        }",
                    "    }",
                    "    public function anchorWithdraw($uid, $diamonds, $to_uid): bool",
                    "    {",
                    "        if (!$this->checkAnchorCanWithdraw($uid, $diamonds, $to_uid)) {",
                    "            return false;",
                    "        }",
                    "        return $this->addProxyUSD($uid, 1, $diamonds, $to_uid);",
                    "    }",
                    "    public function addProxyUSD($uid, $type, $cost, $to_uid)",
                    "    {",
                    "        $ret1 = $diamondModel->decrDiamond($uid, $diamonds, DiamondModel::FLOW_TYPE['WITHDRAW_TO_PROXY'], $coins, 'WITHDRAW_TO_PROXY', $money);",
                    "        $res2 = (new \\Model\\WithdrawModel())->addProxyUSD($uid, $to_uid, $coins, $currency, $money, $diamonds);",
                    "    }",
                ],
            ),
        },
        {
            "file": "waky6_miyouphp/php/config/anchor.php",
            "line": 79,
            "context": _context(
                79,
                [
                    "'diamond_to_usd' => getByTestMode(40000,40000),",
                    "'diamond_to_usd_tr' => getByTestMode(66666.666667,66666.666667),",
                ],
            ),
        },
        {
            "file": "waky6_miyouphp/php/model/WithdrawModel.php",
            "line": 8,
            "context": _context(
                8,
                ["public function addProxyUSD($uid, $to_uid, $coins, $currency, $money, $diamonds = 0)"],
            ),
        },
    ]

    findings = build_deterministic_findings(
        "waky6项目 钻石提现最小兑换金额是多少？有手续费嘛？",
        {"evidence": evidence},
    )
    statements = "\n".join(item["statement"] for item in findings["facts"])

    assert "400,000 钻石" in statements
    assert "600,000 钻石" in statements
    assert "400 钻石" in statements
    assert "没有独立平台手续费" in statements
    assert "10,000 钻石的整数倍" not in findings["conclusion"]


def test_spaceship_fragments_question_answers_full_allocation_flow() -> None:
    """验证年度金主问题能说清等级总量、固定榜、保底池和随机惊喜池。"""
    result = {"evidence": _spaceship_fragment_evidence()}
    findings = build_deterministic_findings(
        "waky6 25年年度金主活动，飞船商品碎片是如何分配的？",
        result,
    )
    categories = {item["category"] for item in findings["facts"]}
    assert {
        "碎片总量与固定榜",
        "分配顺序",
        "保底池",
        "惊喜池",
        "惊喜随机发放",
        "领奖叠加规则",
        "高等级模板",
        "配置比例",
    } <= categories
    statements = " ".join(item["statement"] for item in findings["facts"])
    assert "等级 1 总量 4,000" in statements
    assert "等级 4 总量 16,000" in statements
    assert "固定榜奖励先发放" in statements
    assert "60% 进入保底池" in statements
    assert "40% 进入惊喜池" in statements
    assert "10%、20%、30%" in statements
    assert "随机序列" in statements
    assert "固定榜奖励和保底奖励互斥" in statements
    assert "沿用第 4 级" in statements
    assert "60% 保底、40% 惊喜" in findings["conclusion"]
    assert findings["evidence_refs"]
    assert findings["uncertainties"]


def test_ai_timeout_report_keeps_deterministic_business_answer() -> None:
    """验证 AI 失败警告不会覆盖已经确认的业务限制结论。"""
    evidence = _usd_fuse_evidence()
    result = {
        "evidence": evidence,
        "warnings": ["AI 诊断叙写不可用，已保留确定性业务分析与代码证据"],
        "validation": {},
    }
    result["deterministic_findings"] = build_deterministic_findings(
        "触发美元熔断时，业务有哪些具体限制？",
        result,
    )
    assessment = build_assessment(result)
    assert "美元熔断" in assessment["conclusion"]
    assert "20018" in assessment["conclusion"]
    assert any("作用范围" in item for item in assessment["confirmed_facts"])
    assert "未定位到相关代码证据" not in assessment["unverified_items"]


def test_llm_context_prioritizes_deterministic_source_refs() -> None:
    """验证送入模型的证据先放确定性业务引用，避免被无关文件挤占。"""
    unrelated = {
        "file": "php/unrelated.php",
        "line": 1,
        "content": "unrelated",
    }
    evidence = [unrelated, *_usd_fuse_evidence()]
    result = {"evidence": evidence}
    result["deterministic_findings"] = build_deterministic_findings(
        "触发美元熔断时有哪些限制？",
        result,
    )
    context = build_evidence_context("waky6", "触发美元熔断时有哪些限制？", result)
    prioritized_refs = set(result["deterministic_findings"]["evidence_refs"])
    assert context["evidence"][0]["ref"] in prioritized_refs
    assert context["deterministic_findings"]["facts"]
    assert prioritized_refs <= set(context["allowed_evidence_refs"])


def test_insufficient_retrieval_support_rejects_generic_return_false_facts() -> None:
    """验证业务锚点缺失时不会把无关 return false 拼成已确认业务限制。"""
    result = {
        "evidence": [
            {
                "file": "php/unrelated.php",
                "line": 10,
                "context": _context(10, ["return false;"]),
            }
        ],
        "retrieval_plan": {
            "retrieval_mode": "insufficient_support",
            "retrieval_support": {"grounded": False, "level": "insufficient"},
        },
    }

    findings = build_deterministic_findings("用户风控冻结阈值是多少？", result)

    assert findings["facts"] == []
    assert findings["evidence_refs"] == []
    assert any("停止用泛化词扩展证据" in item for item in findings["uncertainties"])


def test_generic_facts_require_question_concepts_on_the_same_line() -> None:
    """验证兜底控制点必须与问题领域概念同行，普通返回语句不会入选。"""
    result = {
        "evidence": [
            {
                "file": "php/RiskControl.php",
                "line": 20,
                "context": _context(
                    20,
                    [
                        "if ($riskScore >= $riskThreshold) { return false; }",
                        "return false;",
                    ],
                ),
            }
        ],
        "retrieval_plan": {
            "retrieval_mode": "semantic_discovery",
            "retrieval_support": {"grounded": True, "level": "semantic_concepts"},
        },
    }

    findings = build_deterministic_findings("风控阈值如何判断？", result)

    assert len(findings["facts"]) == 1
    assert "$riskThreshold" in findings["facts"][0]["statement"]
