"""
Compatibility layer for pandas_ta using the ta package
"""
import ta.trend as ta_trend
import ta.momentum as ta_momentum
import ta.volatility as ta_volatility
import pandas as pd

class CompatTA:
    """Compatibility class that mimics pandas_ta API using ta package"""
    
    @staticmethod
    def ema(close, length=None, append=False, col_names=None):
        """Exponential Moving Average"""
        if col_names is None:
            col_names = ('EMA',)
        result = ta_trend.EMAIndicator(close, window=length).ema_indicator()
        if append and isinstance(close, pd.DataFrame):
            close[col_names[0]] = result
            return close
        return result
    
    @staticmethod
    def sma(close, length=None, append=False, col_names=None):
        """Simple Moving Average"""
        if col_names is None:
            col_names = ('SMA',)
        result = ta_trend.SMAIndicator(close, window=length).sma_indicator()
        if append and isinstance(close, pd.DataFrame):
            close[col_names[0]] = result
            return close
        return result
    
    @staticmethod
    def rsi(close, length=None, append=False, col_names=None):
        """Relative Strength Index"""
        if col_names is None:
            col_names = ('RSI',)
        result = ta_momentum.RSIIndicator(close, window=length).rsi()
        if append and isinstance(close, pd.DataFrame):
            close[col_names[0]] = result
            return close
        return result
    
    @staticmethod
    def atr(high, low, close, length=None, append=False, col_names=None):
        """Average True Range"""
        if col_names is None:
            col_names = ('ATR',)
        result = ta_volatility.AverageTrueRange(high, low, close, window=length).average_true_range()
        if append and isinstance(high, pd.DataFrame):
            high[col_names[0]] = result
            return high
        return result
    
    @staticmethod
    def adx(high, low, close, length=None, append=False, col_names=None):
        """Average Directional Index"""
        if col_names is None:
            col_names = ('ADX',)
        result = ta_trend.ADXIndicator(high, low, close, window=length).adx()
        if append and isinstance(high, pd.DataFrame):
            high[col_names[0]] = result
            return high
        return result
    
    @staticmethod
    def bbands(close, length=None, append=False, col_names=None):
        """Bollinger Bands"""
        if col_names is None:
            col_names = ('BBL', 'BBM', 'BBU')
        result = ta_volatility.BollingerBands(close, window=length)
        bbl = result.bollinger_lband()
        bbm = result.bollinger_mavg()
        bbu = result.bollinger_hband()
        if append and isinstance(close, pd.DataFrame):
            close[col_names[0]] = bbl
            close[col_names[1]] = bbm
            close[col_names[2]] = bbu
            return close
        return pd.DataFrame({col_names[0]: bbl, col_names[1]: bbm, col_names[2]: bbu})
    
    @staticmethod
    def stoch(high, low, close, k=14, d=3, smooth_k=3, append=False):
        """Stochastic Oscillator"""
        result = ta_momentum.StochasticOscillator(high, low, close, window=k, smooth_window=smooth_k)
        k_val = result.stoch()
        d_val = result.stoch_signal()
        if append and isinstance(high, pd.DataFrame):
            high['STOCH_K'] = k_val
            high['STOCH_D'] = d_val
            return high
        return pd.DataFrame({'STOCH_K': k_val, 'STOCH_D': d_val})

class _PandasTACompat:
    """Main compatibility module that provides df.ta accessor"""
    
    def __init__(self):
        self.CompatTA = CompatTA
    
    def ema(self, *args, **kwargs):
        return CompatTA.ema(*args, **kwargs)
    
    def sma(self, *args, **kwargs):
        return CompatTA.sma(*args, **kwargs)
    
    def rsi(self, *args, **kwargs):
        return CompatTA.rsi(*args, **kwargs)
    
    def atr(self, *args, **kwargs):
        return CompatTA.atr(*args, **kwargs)
    
    def adx(self, *args, **kwargs):
        return CompatTA.adx(*args, **kwargs)
    
    def bbands(self, *args, **kwargs):
        return CompatTA.bbands(*args, **kwargs)
    
    def stoch(self, *args, **kwargs):
        return CompatTA.stoch(*args, **kwargs)

class _DataFrameTA:
    """Provides df.ta accessor like pandas_ta"""
    def __init__(self, df):
        self._df = df
    
    def ema(self, length=None, append=False, col_names=None):
        close = self._df['close']
        result = ta_trend.EMAIndicator(close, window=length).ema_indicator()
        if append:
            name = f'EMA_{length}'  # Nombre esperado por strategy.py
            self._df[name] = result
        return result
    
    def sma(self, length=None, append=False, col_names=None):
        close = self._df['close']
        result = ta_trend.SMAIndicator(close, window=length).sma_indicator()
        if append:
            name = col_names[0] if col_names else 'SMA'
            self._df[name] = result
        return result
    
    def rsi(self, length=None, append=False, col_names=None):
        close = self._df['close']
        result = ta_momentum.RSIIndicator(close, window=length).rsi()
        if append:
            name = f'RSI_{length}'  # Nombre esperado por strategy.py
            self._df[name] = result
        return result
    
    def atr(self, length=None, append=False, col_names=None):
        high = self._df['high']
        low = self._df['low']
        close = self._df['close']
        result = ta_volatility.AverageTrueRange(high, low, close, window=length).average_true_range()
        if append:
            name = f'ATRr_{length}'  # Nombre esperado por strategy.py
            self._df[name] = result
        return result
    
    def adx(self, length=None, append=False, col_names=None):
        high = self._df['high']
        low = self._df['low']
        close = self._df['close']
        result = ta_trend.ADXIndicator(high, low, close, window=length).adx()
        if append:
            name = f'ADX_{length}'  # Nombre esperado por strategy.py
            self._df[name] = result
        return result
    
    def bbands(self, length=None, append=False, col_names=None):
        close = self._df['close']
        result = ta_volatility.BollingerBands(close, window=length)
        bbl = result.bollinger_lband()
        bbm = result.bollinger_mavg()
        bbu = result.bollinger_hband()
        if append:
            # Nombres esperados por strategy.py
            self._df[f'BBL_{length}_2.0'] = bbl
            self._df[f'BBM_{length}_2.0'] = bbm
            self._df[f'BBU_{length}_2.0'] = bbu
        return pd.DataFrame({'BBL': bbl, 'BBM': bbm, 'BBU': bbu})
    
    def stoch(self, k=14, d=3, smooth_k=3, append=False):
        high = self._df['high']
        low = self._df['low']
        close = self._df['close']
        result = ta_momentum.StochasticOscillator(high, low, close, window=k, smooth_window=smooth_k)
        k_val = result.stoch()
        d_val = result.stoch_signal()
        if append:
            # Nombres esperados por strategy.py
            self._df[f'STOCHk_{k}_{smooth_k}_{smooth_k}'] = k_val
            self._df[f'STOCHd_{k}_{smooth_k}_{smooth_k}'] = d_val
        return pd.DataFrame({'STOCH_K': k_val, 'STOCH_D': d_val})

def __getattr__(name):
    """Make module functions available at module level"""
    if name == 'ema':
        return CompatTA.ema
    elif name == 'sma':
        return CompatTA.sma
    elif name == 'rsi':
        return CompatTA.rsi
    elif name == 'atr':
        return CompatTA.atr
    elif name == 'adx':
        return CompatTA.adx
    elif name == 'bbands':
        return CompatTA.bbands
    elif name == 'stoch':
        return CompatTA.stoch
    raise AttributeError(f"module 'pandas_ta' has no attribute '{name}'")

# Monkey-patch pd.DataFrame and pandas.core.frame.DataFrame to intercept 'ta' access
import pandas.core.frame as pdf

_original_df = pd.DataFrame
_original_core_df = pdf.DataFrame

class _PatchedDataFrame(_original_df):  # type: ignore[misc, valid-type]
    def __getattribute__(self, name):
        if name == 'ta':
            return _DataFrameTA(self)
        return object.__getattribute__(self, name)
    
    @property
    def ta(self):
        return _DataFrameTA(self)

pd.DataFrame = _PatchedDataFrame
pdf.DataFrame = _PatchedDataFrame

# Export compatibility module
pd.ta = _PandasTACompat()
