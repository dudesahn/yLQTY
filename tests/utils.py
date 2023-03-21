import pytest
import brownie
from brownie import interface, chain, accounts

# returns (profit, loss) of a harvest
def harvest_strategy(
    use_yswaps,
    strategy,
    token,
    gov,
    profit_whale,
    profit_amount,
    destination_strategy,
):

    # reset everything with a sleep and mine
    chain.sleep(1)
    chain.mine(1)

    # add in any custom logic needed here, for instance with router strategy (also reason we have a destination strategy).
    # also add in any custom logic needed to get raw reward assets to the strategy (like for liquity)

    ####### ADD LOGIC AS NEEDED FOR CLAIMING/SENDING REWARDS TO STRATEGY #######
    # usually this is automatic, but it may need to be externally triggered

    # send WETH and LUSD to the strategy
    # op bridge, also has ether
    lusd_whale = accounts.at("0x99C9fc46f92E8a1c0deC1b1747d010903E884bE1", force=True)
    # this check makes sure only send rewards when they actually would have been earned
    if use_yswaps and strategy.stakedBalance() > 0:
        # liquity doesn't do a good job of claiming
        lusd = interface.IERC20(strategy.lusd())
        lusd.transfer(strategy, 200e18, {"from": lusd_whale})
        lusd_whale.transfer(strategy, 5e17)
        assert strategy.balance() > 0
        print("Reward tokens sent to strategy")

    # if we have no staked assets, and we are taking profit (when closing out a strategy) then we will need to ignore health check
    if strategy.stakedBalance() == 0:
        strategy.setDoHealthCheck(False, {"from": gov})

    # when in emergency exit we don't enter prepare return, so we need to manually swap our ether for WETH
    if strategy.emergencyExit():
        weth = interface.IWETH(strategy.weth())
        to_deposit = strategy.balance()
        weth.deposit({"from": strategy, "value": to_deposit})

    # we can use the tx for debugging if needed
    tx = strategy.harvest({"from": gov})
    profit = tx.events["Harvested"]["profit"] / (10 ** token.decimals())
    loss = tx.events["Harvested"]["loss"] / (10 ** token.decimals())

    # assert there are no loose funds in strategy after a harvest, and that we don't have ether left
    assert strategy.balanceOfWant() == 0
    assert strategy.balance() == 0

    # our trade handler takes action, sending out rewards tokens and sending back in profit
    if use_yswaps:
        trade_handler_action(strategy, token, gov, profit_whale, profit_amount)

    # reset everything with a sleep and mine
    chain.sleep(1)
    chain.mine(1)

    # return our profit, loss
    return (profit, loss)


# simulate the trade handler sweeping out assets and sending back profit
def trade_handler_action(
    strategy,
    token,
    gov,
    profit_whale,
    profit_amount,
):
    ####### ADD LOGIC AS NEEDED FOR SENDING REWARDS OUT AND PROFITS IN #######
    # get our tokens from our strategy
    lusd = interface.IERC20(strategy.lusd())
    weth = interface.IERC20(strategy.weth())

    lusdBalance = lusd.balanceOf(strategy)
    if lusdBalance > 0:
        lusd.transfer(gov, lusdBalance, {"from": strategy})
        print("LUSD rewards present")
        assert lusd.balanceOf(strategy) == 0

    wethBalance = weth.balanceOf(strategy)
    if wethBalance > 0:
        weth.transfer(gov, wethBalance, {"from": strategy})
        print("WETH rewards present")
        assert weth.balanceOf(strategy) == 0

    # send our profits back in
    if lusdBalance > 0 or wethBalance > 0:
        token.transfer(strategy, profit_amount, {"from": profit_whale})
        print("Rewards converted into profit and returned")


# do a check on our strategy and vault of choice
def check_status(
    strategy,
    vault,
):
    # check our current status
    strategy_params = vault.strategies(strategy)
    vault_assets = vault.totalAssets()
    debt_outstanding = vault.debtOutstanding(strategy)
    credit_available = vault.creditAvailable(strategy)
    total_debt = vault.totalDebt()
    share_price = vault.pricePerShare()
    strategy_debt = strategy_params["totalDebt"]
    strategy_loss = strategy_params["totalLoss"]
    strategy_gain = strategy_params["totalGain"]
    strategy_debt_ratio = strategy_params["debtRatio"]
    strategy_assets = strategy.estimatedTotalAssets()

    # print our stuff
    print("Vault Assets:", vault_assets)
    print("Strategy Debt Outstanding:", debt_outstanding)
    print("Strategy Credit Available:", credit_available)
    print("Vault Total Debt:", total_debt)
    print("Vault Share Price:", share_price)
    print("Strategy Total Debt:", strategy_debt)
    print("Strategy Total Loss:", strategy_loss)
    print("Strategy Total Gain:", strategy_gain)
    print("Strategy Debt Ratio:", strategy_debt_ratio)
    print("Strategy Estimated Total Assets:", strategy_assets, "\n")

    # print simplified versions if we have something more than dust
    token = interface.IERC20(vault.token())
    if vault_assets > 10:
        print(
            "Decimal-Corrected Vault Assets:", vault_assets / (10 ** token.decimals())
        )
    if debt_outstanding > 10:
        print(
            "Decimal-Corrected Strategy Debt Outstanding:",
            debt_outstanding / (10 ** token.decimals()),
        )
    if credit_available > 10:
        print(
            "Decimal-Corrected Strategy Credit Available:",
            credit_available / (10 ** token.decimals()),
        )
    if total_debt > 10:
        print(
            "Decimal-Corrected Vault Total Debt:", total_debt / (10 ** token.decimals())
        )
    if share_price > 10:
        print("Decimal-Corrected Share Price:", share_price / (10 ** token.decimals()))
    if strategy_debt > 10:
        print(
            "Decimal-Corrected Strategy Total Debt:",
            strategy_debt / (10 ** token.decimals()),
        )
    if strategy_loss > 10:
        print(
            "Decimal-Corrected Strategy Total Loss:",
            strategy_loss / (10 ** token.decimals()),
        )
    if strategy_gain > 10:
        print(
            "Decimal-Corrected Strategy Total Gain:",
            strategy_gain / (10 ** token.decimals()),
        )
    if strategy_assets > 10:
        print(
            "Decimal-Corrected Strategy Total Assets:",
            strategy_assets / (10 ** token.decimals()),
        )

    return strategy_params
