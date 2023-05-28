// SPDX-License-Identifier: AGPL-3.0
pragma solidity ^0.8.15;

// These are the core Yearn libraries
import "@openzeppelin/contracts/utils/math/Math.sol";
import "@yearnvaults/contracts/BaseStrategy.sol";

interface ITradeFactory {
    function enable(address, address) external;

    function disable(address, address) external;
}

interface IOracle {
    function latestRoundData(
        address,
        address
    )
        external
        view
        returns (
            uint80 roundId,
            uint256 answer,
            uint256 startedAt,
            uint256 updatedAt,
            uint80 answeredInRound
        );
}

interface IWeth {
    function deposit() external payable;
}

interface IBooster {
    function strategyHarvest() external;
}

interface ILiquityStaking {
    function stake(uint _LQTYamount) external;

    function unstake(uint _LQTYamount) external;

    function getPendingETHGain(address _user) external view returns (uint);

    function getPendingLUSDGain(address _user) external view returns (uint);

    function stakes(address _user) external view returns (uint);
}

contract StrategyLQTYStaker is BaseStrategy {
    using SafeERC20 for IERC20;
    /* ========== STATE VARIABLES ========== */

    /// @notice LQTY staking contract
    ILiquityStaking public constant lqtyStaking =
        ILiquityStaking(0x4f9Fbb3f1E99B56e0Fe2892e623Ed36A76Fc605d);

    /// @notice The percentage of LQTY from each harvest that we send to yearn's secondary staker to boost yields.
    uint256 public keepLQTY;

    /// @notice The address of our Liquity booster. This is where we send any keepLQTY.
    IBooster public liquityBooster;

    // this means all of our fee values are in basis points
    uint256 internal constant FEE_DENOMINATOR = 10000;

    /// @notice Address of our main rewards token, LUSD
    IERC20 public constant lusd =
        IERC20(0x5f98805A4E8be255a32880FDeC7F6728C6568bA0);

    /// @notice Convert our ether rewards into weth for easier swaps
    IERC20 public constant weth =
        IERC20(0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2);

    /// @notice LQTY token address.
    IERC20 public constant lqty =
        IERC20(0x6DEA81C8171D0bA574754EF6F8b412F2Ed88c54D);

    /// @notice Minimum profit size in USDC that we want to harvest.
    /// @dev Only used in harvestTrigger.
    uint256 public harvestProfitMinInUsdc;

    /// @notice Maximum profit size in USDC that we want to harvest (ignore gas price once we get here).
    /// @dev Only used in harvestTrigger.
    uint256 public harvestProfitMaxInUsdc;

    // ySwaps stuff
    /// @notice The address of our ySwaps trade factory.
    address public tradeFactory;

    /// @notice Array of any rewards tokens used for our tradehandler.
    address[] public rewardsTokens;

    /* ========== CONSTRUCTOR ========== */

    constructor(
        address _vault,
        address _tradeFactory,
        uint256 _harvestProfitMinInUsdc,
        uint256 _harvestProfitMaxInUsdc
    ) BaseStrategy(_vault) {
        // make sure that we haven't initialized this before
        if (tradeFactory != address(0)) {
            revert(); // already initialized.
        }

        // 1:1 assignments
        tradeFactory = _tradeFactory;
        harvestProfitMinInUsdc = _harvestProfitMinInUsdc;
        harvestProfitMaxInUsdc = _harvestProfitMaxInUsdc;

        // want = LQTY
        want.approve(address(lqtyStaking), type(uint256).max);

        // set up our max delay
        maxReportDelay = 30 days;

        // set up rewards and trade factory
        rewardsTokens = [address(weth), address(lusd)];
        _setUpTradeFactory();

        // set keep to 5%
        keepLQTY = 500;
    }

    /* ========== VIEWS ========== */

    /// @notice Strategy name.
    function name() external view override returns (string memory) {
        return "StrategyLQTYStaker";
    }

    /// @notice Balance of want staked in Liquity's staking contract.
    function stakedBalance() public view returns (uint256) {
        return lqtyStaking.stakes(address(this));
    }

    /// @notice Balance of want sitting in our strategy.
    function balanceOfWant() public view returns (uint256) {
        return want.balanceOf(address(this));
    }

    /// @notice Total assets the strategy holds, sum of loose and staked want.
    function estimatedTotalAssets() public view override returns (uint256) {
        return balanceOfWant() + stakedBalance();
    }

    /* ========== CORE STRATEGY FUNCTIONS ========== */

    function prepareReturn(
        uint256 _debtOutstanding
    )
        internal
        override
        returns (uint256 _profit, uint256 _loss, uint256 _debtPayment)
    {
        // rewards will be converted later with mev protection by yswaps (tradeFactory)
        // if we have anything staked, harvest our rewards. can't claim rewards without a stake.
        uint256 _stakedBal = stakedBalance();
        if (_stakedBal > 0) {
            lqtyStaking.unstake(0);
        }

        // convert our ether to weth if we have any
        uint256 ethBalance = address(this).balance;
        if (ethBalance > 0) {
            IWeth(address(weth)).deposit{value: ethBalance}();
        }

        // send some LQTY to our booster and claim accrued yield from it
        uint256 _keepLQTY = keepLQTY;
        address _liquityBooster = address(liquityBooster);
        if (_keepLQTY > 0 && _liquityBooster != address(0)) {
            uint256 lqtyBalance = lqty.balanceOf(address(this));
            uint256 _sendToBooster;
            unchecked {
                _sendToBooster = (lqtyBalance * _keepLQTY) / FEE_DENOMINATOR;
            }
            if (_sendToBooster > 0) {
                lqty.safeTransfer(_liquityBooster, _sendToBooster);
                liquityBooster.strategyHarvest();
            }
        }

        // serious loss should never happen, but if it does, let's record it accurately
        uint256 assets = estimatedTotalAssets();
        uint256 debt = vault.strategies(address(this)).totalDebt;

        // if assets are greater than debt, things are working great!
        if (assets >= debt) {
            unchecked {
                _profit = assets - debt;
            }
            _debtPayment = _debtOutstanding;

            uint256 toFree = _profit + _debtPayment;

            // freed is math.min(wantBalance, toFree)
            (uint256 freed, ) = liquidatePosition(toFree);

            if (toFree > freed) {
                if (_debtPayment > freed) {
                    _debtPayment = freed;
                    _profit = 0;
                } else {
                    unchecked {
                        _profit = freed - _debtPayment;
                    }
                }
            }
        }
        // if assets are less than debt, we are in trouble. don't worry about withdrawing here, just report losses
        else {
            unchecked {
                _loss = debt - assets;
            }
        }
    }

    function adjustPosition(uint256 _debtOutstanding) internal override {
        // if in emergency exit, we don't want to deploy any more funds
        if (emergencyExit) {
            return;
        }

        // Send all of our LP tokens to the proxy and deposit to the gauge
        uint256 _toInvest = balanceOfWant();
        if (_toInvest > 0) {
            lqtyStaking.stake(_toInvest);
        }
    }

    function liquidatePosition(
        uint256 _amountNeeded
    ) internal override returns (uint256 _liquidatedAmount, uint256 _loss) {
        // check our loose want
        uint256 _wantBal = balanceOfWant();
        if (_amountNeeded > _wantBal) {
            uint256 _stakedBal = stakedBalance();
            if (_stakedBal > 0) {
                uint256 _neededFromStaked;
                unchecked {
                    _neededFromStaked = _amountNeeded - _wantBal;
                }
                // withdraw whatever extra funds we need
                // normally we would do min(staked, _neededFromStaked) but liquity already does that for us
                lqtyStaking.unstake(_neededFromStaked);
                _wantBal = balanceOfWant();
            }
            _liquidatedAmount = Math.min(_amountNeeded, _wantBal);
            unchecked {
                _loss = _amountNeeded - _liquidatedAmount;
            }
        } else {
            // we have enough balance to cover the liquidation available
            return (_amountNeeded, 0);
        }
    }

    // fire sale, get rid of it all!
    function liquidateAllPositions() internal override returns (uint256) {
        uint256 _stakedBal = stakedBalance();
        if (_stakedBal > 0) {
            // don't bother withdrawing zero, save gas where we can
            lqtyStaking.unstake(_stakedBal);
        }

        return balanceOfWant();
    }

    // migrate our want token to a new strategy if needed, as well as any LUSD or WETH
    function prepareMigration(address _newStrategy) internal override {
        uint256 _stakedBal = stakedBalance();
        if (_stakedBal > 0) {
            lqtyStaking.unstake(_stakedBal);
        }
        uint256 lusdBalance = lusd.balanceOf(address(this));
        uint256 ethBalance = address(this).balance;

        if (lusdBalance > 0) {
            lusd.safeTransfer(_newStrategy, lusdBalance);
        }

        if (ethBalance > 0) {
            IWeth(address(weth)).deposit{value: ethBalance}();
            weth.safeTransfer(_newStrategy, ethBalance);
        }
    }

    // want is blocked by default, add any other tokens to protect from gov here.
    function protectedTokens()
        internal
        view
        override
        returns (address[] memory)
    {}

    /* ========== YSWAPS ========== */

    /// @notice Use to add or update rewards, rebuilds tradefactory too
    /// @dev Do this before updating trade factory if we have extra rewards.
    ///  Can only be called by governance.
    /// @param _rewards Rewards tokens to add to our trade factory.
    function updateRewards(address[] memory _rewards) external onlyGovernance {
        address tf = tradeFactory;
        _removeTradeFactoryPermissions(true);
        _updateRewards(_rewards);

        tradeFactory = tf;
        _setUpTradeFactory();
    }

    function _updateRewards(address[] memory _rewardsTokens) internal {
        // empty the rewardsTokens and rebuild
        delete rewardsTokens;
        rewardsTokens = _rewardsTokens;
    }

    /// @notice Use to update our trade factory.
    /// @dev Can only be called by governance.
    /// @param _newTradeFactory Address of new trade factory.
    function updateTradeFactory(
        address _newTradeFactory
    ) external onlyGovernance {
        require(
            _newTradeFactory != address(0),
            "Can't remove with this function"
        );
        _removeTradeFactoryPermissions(true);
        tradeFactory = _newTradeFactory;
        _setUpTradeFactory();
    }

    function _setUpTradeFactory() internal {
        // approve and set up trade factory
        address _tradeFactory = tradeFactory;
        address _want = address(want);

        ITradeFactory tf = ITradeFactory(_tradeFactory);

        // enable for all rewards tokens too
        for (uint256 i; i < rewardsTokens.length; ++i) {
            address _rewardsToken = rewardsTokens[i];
            IERC20(_rewardsToken).approve(_tradeFactory, type(uint256).max);
            tf.enable(_rewardsToken, _want);
        }
    }

    /// @notice Use this to remove permissions from our current trade factory.
    /// @dev Once this is called, setUpTradeFactory must be called to get things working again.
    /// @param _disableTf Specify whether to disable the tradefactory when removing.
    ///  Option given in case we need to get around a reverting disable.
    function removeTradeFactoryPermissions(
        bool _disableTf
    ) external onlyVaultManagers {
        _removeTradeFactoryPermissions(_disableTf);
    }

    function _removeTradeFactoryPermissions(bool _disableTf) internal {
        address _tradeFactory = tradeFactory;
        if (_tradeFactory == address(0)) {
            return;
        }
        ITradeFactory tf = ITradeFactory(_tradeFactory);
        address _want = address(want);

        // disable for all rewards tokens too
        for (uint256 i; i < rewardsTokens.length; ++i) {
            address _rewardsToken = rewardsTokens[i];
            IERC20(_rewardsToken).approve(_tradeFactory, 0);
            if (_disableTf) {
                tf.disable(_rewardsToken, _want);
            }
        }

        tradeFactory = address(0);
    }

    /* ========== KEEP3RS ========== */

    /**
     * @notice
     *  Provide a signal to the keeper that harvest() should be called.
     *
     *  Don't harvest if a strategy is inactive.
     *  If our profit exceeds our upper limit, then harvest no matter what. For
     *  our lower profit limit, credit threshold, max delay, and manual force trigger,
     *  only harvest if our gas price is acceptable.
     *
     * @param callCostinEth The keeper's estimated gas cost to call harvest() (in wei).
     * @return True if harvest() should be called, false otherwise.
     */
    function harvestTrigger(
        uint256 callCostinEth
    ) public view override returns (bool) {
        // Should not trigger if strategy is not active (no assets and no debtRatio). This means we don't need to adjust keeper job.
        if (!isActive()) {
            return false;
        }

        // harvest if we have a profit to claim at our upper limit without considering gas price
        uint256 claimableProfit = claimableProfitInUsdc();
        if (claimableProfit > harvestProfitMaxInUsdc) {
            return true;
        }

        // check if the base fee gas price is higher than we allow. if it is, block harvests.
        if (!isBaseFeeAcceptable()) {
            return false;
        }

        // trigger if we want to manually harvest, but only if our gas price is acceptable
        if (forceHarvestTriggerOnce) {
            return true;
        }

        // harvest if we have a sufficient profit to claim, but only if our gas price is acceptable
        if (claimableProfit > harvestProfitMinInUsdc) {
            return true;
        }

        StrategyParams memory params = vault.strategies(address(this));
        // harvest regardless of profit once we reach our maxDelay
        if (block.timestamp - params.lastReport > maxReportDelay) {
            return true;
        }

        // harvest our credit if it's above our threshold
        if (vault.creditAvailable() > creditThreshold) {
            return true;
        }

        // otherwise, we don't harvest
        return false;
    }

    /// @notice Calculates the profit if all claimable assets were sold for USDC (6 decimals).
    /// @dev Uses chainlink's price oracle for ETH price, assumes $1 for LUSD.
    /// @return Total return in USDC from selling claimable LUSD and ETH.
    function claimableProfitInUsdc() public view returns (uint256) {
        (, uint256 wethPrice, , , ) = IOracle(
            0x47Fb2585D2C56Fe188D0E6ec628a38b74fCeeeDf
        ).latestRoundData(
                0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE,
                address(0x0000000000000000000000000000000000000348) // USD, returns 1e8
            );

        uint256 claimableLusd = lqtyStaking.getPendingLUSDGain(address(this));
        uint256 claimableETH = lqtyStaking.getPendingETHGain(address(this));

        // Oracle returns prices as 8 decimals, so multiply by claimable amount and divide by 1e20 to get 1e6 result
        return (1e8 * claimableLusd + wethPrice * claimableETH) / 1e20;
    }

    /// @notice Convert our keeper's eth cost into want
    /// @dev We don't use this since we don't factor call cost into our harvestTrigger.
    /// @param _ethAmount Amount of ether spent.
    /// @return Value of ether in want.
    function ethToWant(
        uint256 _ethAmount
    ) public view override returns (uint256) {}

    // include so our contract plays nicely with ether
    receive() external payable {}

    /* ========== SETTERS ========== */
    // These functions are useful for setting parameters of the strategy that may need to be adjusted.

    /// @notice Use this to set or update our keep amounts for this strategy.
    /// @dev Must be less than 1,000. Set in basis points. Only governance can set this.
    /// @param _keepLqty Percent of LQTY from each harvest to send to our booster.
    function setKeepLqty(uint256 _keepLqty) external onlyGovernance {
        if (_keepLqty > 1000) {
            revert();
        }
        if (_keepLqty > 0 && address(liquityBooster) == address(0)) {
            revert();
        }
        keepLQTY = _keepLqty;
    }

    /// @notice Use this to set or update our booster contract.
    /// @dev This is where we send our keepLQTY to compound rewards
    ///  Only governance can set this.
    /// @param _booster Address of our liquity booster.
    function setBooster(address _booster) external onlyGovernance {
        liquityBooster = IBooster(_booster);
    }

    /**
     * @notice
     *  Here we set various parameters to optimize our harvestTrigger.
     * @param _harvestProfitMinInUsdc The amount of profit (in USDC, 6 decimals)
     *  that will trigger a harvest if gas price is acceptable.
     * @param _harvestProfitMaxInUsdc The amount of profit in USDC that
     *  will trigger a harvest regardless of gas price.
     */
    function setHarvestTriggerParams(
        uint256 _harvestProfitMinInUsdc,
        uint256 _harvestProfitMaxInUsdc
    ) external onlyVaultManagers {
        harvestProfitMinInUsdc = _harvestProfitMinInUsdc;
        harvestProfitMaxInUsdc = _harvestProfitMaxInUsdc;
    }
}
